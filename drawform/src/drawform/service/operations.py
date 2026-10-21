"""已确认的试点监测：输出本地事件，不发外部消息、不删除数据、不重启服务。"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from drawform.service import maintenance
from drawform.service.policy import (HEALTH_INTERVAL, STORAGE_INTERVAL, HEALTH_FAILURES,
    WARNING_REPEAT, CRITICAL_REPEAT, DISK_WARNING_USED, DISK_CRITICAL_USED,
    DISK_WARNING_FREE, DISK_CRITICAL_FREE)
from drawform.service.production import INCOMPLETE_RESTORE
from drawform.service.retention import instant


def _issue(level, message):
    return {"level": level, "message": message}


def disk_issue(total, used, free):
    if total <= 0 or min(used, free) < 0:
        raise ValueError("磁盘采样无效")
    if used / total >= DISK_CRITICAL_USED or free < DISK_CRITICAL_FREE:
        return _issue("critical", "磁盘已用达到90%或剩余不足5GiB")
    if used / total >= DISK_WARNING_USED or free < DISK_WARNING_FREE:
        return _issue("warning", "磁盘已用达到80%或剩余不足10GiB")
    return None


def _probe(url, ca):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("探测地址须为无口令、查询串或片段的 HTTPS 服务地址")
    token = os.environ.get("DRAWFORM_TOKEN", "").strip()
    if not token:
        raise ValueError("缺少 DRAWFORM_TOKEN；令牌只从环境读取")
    context = ssl.create_default_context(cafile=str(ca) if ca else None)
    client = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    request = Request(url.rstrip('/') + '/jobs', headers={"Authorization": 'Bearer ' + token})
    try:
        with client.open(request, timeout=5) as response:
            body = json.loads(response.read())
            return response.status == 200 and isinstance(body, dict) and isinstance(body.get('jobs'), list)
    except (OSError, ValueError):
        return False


def _process_exists(pid):
    if type(pid) is not int or pid <= 1:
        raise ValueError("须提供被监测服务的实际 PID，不能使用0或1")
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid='], capture_output=True, text=True, timeout=5)
    return str(pid) in result.stdout.split()


@contextmanager
def _state(path, work=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("状态文件不能是符号链接")
    path = path.resolve()
    if work and path.is_relative_to(Path(work).resolve()):
        raise ValueError("运维状态和事件日志须放在业务工作目录之外")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_suffix('.lock')
    if lock_path.is_symlink():
        raise ValueError("状态锁不能是符号链接")
    with lock_path.open('a') as lease:
        lock_path.chmod(0o600)
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError("运维状态损坏，不能静默重置")
        yield state, path
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            json.dump(state, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)


def _event_log(path, at):
    day = datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%d')
    return path.with_name(path.stem + '.events.' + day + '.log')


def _emit(path, events):
    if not events:
        return
    # 按 UTC 日分文件，避免活跃日志不断更新 mtime 而永远无法列入30天复核。
    for event in events:
        log = _event_log(path, event['at'])
        if log.is_symlink():
            raise ValueError("事件日志不能是符号链接")
        with log.open('a', encoding='utf-8') as handle:
            log.chmod(0o600)
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())


def _notifications(state, issues, now, suppressed=()):
    previous = state.setdefault('active', {})
    events = []
    for key, issue in issues.items():
        old = previous.get(key)
        repeat = CRITICAL_REPEAT if issue['level'] == 'critical' else WARNING_REPEAT
        changed = old is None or old['level'] != issue['level']
        if key not in suppressed and (changed or now - old['last_sent'] >= repeat):
            events.append({'at': now, 'incident': key, 'event': 'opened' if changed else 'reminder', **issue})
            previous[key] = {**issue, 'last_sent': now}
    for key in list(previous):
        if key not in issues and key not in suppressed:
            events.append({'at': now, 'incident': key, 'event': 'recovered', 'level': previous[key]['level']})
            del previous[key]
    return events


def _restore_interrupted(work):
    if not (work / INCOMPLETE_RESTORE).exists():
        return False
    lock = work / '.service.lock'
    if not lock.exists():
        return True
    with lock.open('rb') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False  # 恢复进程仍持锁，不能把正在恢复报成中断。
        fcntl.flock(handle, fcntl.LOCK_UN)
    return True


def check(work, state_path, url, ca=None, pid=None, maintenance_until=None, *, now=None):
    now = time.time() if now is None else now
    work = maintenance._root(work)
    in_maintenance = maintenance_until is not None and now < instant(maintenance_until).timestamp()
    with _state(state_path, work) as (state, path):
        issues = dict(state.get('operation_issues', {}))
        suppressed = {'service_unavailable', 'process_exited'} if in_maintenance else set()
        if not in_maintenance:
            if pid is not None and not _process_exists(pid):
                issues['process_exited'] = _issue('critical', '被监测的服务进程已退出，请核对服务日志')
            if now - state.get('last_health', float('-inf')) >= HEALTH_INTERVAL:
                healthy = _probe(url, ca)
                state['last_health'] = now
                state['health_failures'] = 0 if healthy else state.get('health_failures', 0) + 1
                state['healthy'] = healthy
            if (not state.get('healthy', True) and (state.get('health_failures', 0) >= HEALTH_FAILURES
                    or 'service_unavailable' in state.get('active', {}))):
                issues['service_unavailable'] = _issue('critical', 'HTTPS鉴权探测连续三次失败')
        else:
            state['health_failures'] = 0
            state.pop('last_health', None)
        if (work / 'jobs.jsonl').is_symlink() or (work / 'reviews.jsonl').is_symlink():
            raise ValueError('业务索引或审计不能是符号链接')
        raw = maintenance._read(work / 'jobs.jsonl')
        rows, damaged = maintenance._index_rows(raw)
        latest = {row['job']: row for _line, row in rows}
        for job, row in latest.items():
            if row['state'] == 'crashed':
                issues['job_crashed:' + job] = _issue('critical', f'任务 {job} 程序异常；不是正常交付门拦截')
        if _restore_interrupted(work):
            issues['restore_interrupted'] = _issue('critical', '恢复标记尚在且无恢复进程持锁，目录不可启动')
        if now - state.get('last_storage', float('-inf')) >= STORAGE_INTERVAL:
            storage_issues = {}
            try:
                observed = maintenance.inspect(work)
                disk = disk_issue(**observed['disk'])
                if disk:storage_issues['disk_space'] = disk
                suspect = {'index':observed['index_damaged_lines'], 'audit':observed['audit_damaged_lines'],
                           'missing':observed['missing_files']}
            except (OSError, ValueError) as error:
                suspect = {'inspection_error':str(error)}
            busy = any(r['state'] in ('queued', 'running') for r in latest.values())
            signature = hashlib.sha256(json.dumps(suspect, sort_keys=True).encode()).hexdigest()
            bad = any(suspect.values())
            old_integrity = state.get('storage_issues', {}).get('data_integrity')
            if old_integrity and (bad or busy):
                storage_issues['data_integrity'] = old_integrity
            elif bad and not busy and state.get('integrity_suspect') == signature:
                storage_issues['data_integrity'] = _issue('critical', '非写入期间复查仍有缺文件、坏索引或坏审计')
            state['integrity_suspect'] = signature if bad and not busy else None
            state['storage_issues'] = storage_issues
            state['last_storage'] = now
        issues.update(state.get('storage_issues', {}))
        # 维护窗口暂停服务告警；不伪造已恢复事件。
        for key in suppressed:
            if key in state.get('active', {}):
                issues[key] = {k:state['active'][key][k] for k in ('level','message')}
        events = _notifications(state, issues, now, suppressed)
        state['last_check'] = now
        _emit(path, events)
        return {'events': events, 'active': issues, 'maintenance': in_maintenance,
                'refused_jobs': sum(r['state'] == 'refused' for r in latest.values()),
                'index_damaged_lines': damaged,
                'integrity_recheck_pending': bool(state.get('integrity_suspect')) and 'data_integrity' not in issues,
                'event_log': str(_event_log(path, now))}


def acknowledge(state_path, incident, operator, note):
    if not operator.strip() or not note.strip():
        raise ValueError("须记录处置人和处置说明")
    with _state(state_path) as (state, path):
        if incident not in state.get('active', {}):
            raise ValueError("不存在该活动告警")
        state['active'][incident]['acknowledged'] = True
        event = {'at':time.time(),'incident':incident,'event':'acknowledged','operator':operator,'note':note}
        _emit(path,[event])
        return event


def storage_action(action, source, target, state_path, book=None):
    """显式停服备份或恢复演练；只创建新的副本，绝不替用户停止进程。"""
    error = None
    result = None
    for directory in (source, target):
        if directory and Path(state_path).resolve().is_relative_to(Path(directory).resolve()):
            raise ValueError('运维状态必须放在来源和副本目录之外')
    key = action + ':' + hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest()[:12]
    try:
        if action == 'backup':
            if book is None:
                raise ValueError('试点备份须提供 --book，一起保护审图台账')
            result = maintenance.backup(source, target, book=book)
            maintenance.verify_backup(target)
        elif action == 'verify':
            result = maintenance.verify_backup(source)
            if json.loads((Path(source)/'manifest.json').read_text()).get('pilot_ledger') != maintenance.PILOT_LEDGER:
                raise ValueError('这份备份未包含试点台账，不是完整试点证据')
        elif action == 'restore-check':
            maintenance.verify_backup(source)
            if json.loads((Path(source)/'manifest.json').read_text()).get('pilot_ledger') != maintenance.PILOT_LEDGER:
                raise ValueError('这份备份未包含试点台账，不能作为完整试点恢复')
            result = maintenance.restore(source, target)
            # 从实际落盘的新目录读回，原备份和业务目录均不改变。
            checked = maintenance.inspect(target)
            if checked['missing_files'] or checked['index_damaged_lines'] or checked['audit_damaged_lines']:
                raise ValueError('恢复副本读回有缺口')
            result['restored_book'] = str(Path(target).resolve()/maintenance.PILOT_LEDGER)
        else:
            raise ValueError('未知存储操作')
    except (OSError, ValueError, RuntimeError) as failed:
        error = str(failed)
    with _state(state_path, source) as (state, path):
        issues = state.setdefault('operation_issues', {})
        if error:
            issues[key] = _issue('critical', f'{action} 失败：{error}')
        else:
            issues.pop(key, None)
            state.setdefault('last_success', {})[action] = datetime.now(timezone.utc).isoformat()
        active = {k:{field:v[field] for field in ('level','message')} for k,v in state.get('active',{}).items()
                  if k != key}
        active.update(issues)
        events = _notifications(state, active, time.time())
        _emit(path, events)
    if error:
        raise ValueError(error)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check','watch'):
        child = commands.add_parser(name)
        child.add_argument('--work', required=True)
        child.add_argument('--state', required=True)
        child.add_argument('--url', required=True)
        child.add_argument('--ca')
        child.add_argument('--pid', type=int)
        child.add_argument('--maintenance-until', help='带时区的计划维护截止时间')
    for name in ('backup','verify','restore-check'):
        child = commands.add_parser(name)
        child.add_argument('--source', required=True)
        child.add_argument('--state', required=True)
        if name != 'verify':child.add_argument('--target', required=True)
        if name == 'backup':child.add_argument('--book', required=True)
    ack = commands.add_parser('acknowledge')
    for name in ('state','incident','operator','note'):ack.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ('check','watch'):
            while True:
                result = check(args.work,args.state,args.url,args.ca,args.pid,args.maintenance_until)
                if args.command == 'check' or result['events']:
                    print(json.dumps(result,ensure_ascii=False),flush=True)
                if args.command == 'check':break
                time.sleep(1)  # 进程/任务事件；HTTP与磁盘分别遵守60/300秒采样。
        elif args.command == 'acknowledge':
            print(json.dumps(acknowledge(args.state,args.incident,args.operator,args.note),ensure_ascii=False))
        else:
            print(json.dumps(storage_action(args.command,args.source,getattr(args,'target',None),args.state,
                                            getattr(args,'book',None)),ensure_ascii=False))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
