"""HTTP 层：上传 STEP、查任务状态、取文件或取判定清单。

这一层只做搬运与边界防护，任何几何判断都不在这里 —— 判断在门里，
门的结果原样传出去就是接口里最有价值的那部分。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import pathlib
import tempfile
import zipfile
from dataclasses import asdict

from flask import Flask, abort, g, jsonify, request, send_file

from drawform import knowledge
from drawform.eval.amend_diff import compare
from drawform.review import (
    ACCEPTED,
    NOTE_REQUIRED_REASONS,
    REASON_GROUPS,
    REASONS,
    ReviewError,
    ReviewLog,
    is_reviewable,
)
from drawform.service.jobs import (
    DRAWN,
    WAITING_REFERENCE,
    JobQueue,
    StaleBase,
    head_of,
    current_files,
    latest_answers,
    answers_of,
    versions_of,
)
from drawform.service.answers import Answer, AnswerError, MAX_NOTE_LENGTH
from drawform.geometry.references import ReferenceInputError, BASIS_VERSION
from drawform.service.interaction import (display_name, metadata, task_context,
    reference_receipt, read_source_names, submit_review, ReviewConflict)

#: 存储名固定；客户端文件名仅作经过清理的显示信息，不参与路径。任务目录由服务自己编号。
_STORED_NAME = "model.step"

#: 单个 STEP 的上限。真实零件文件在几百 KB 到几 MB 之间，64 MB 已经很宽松，
#: 但"没有上限"和"上限给大了"是两种性质完全不同的问题。
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

#: 默认读环境变量，方便部署时不把令牌写进命令行（会进 shell 历史与进程列表）。
#: 支持逗号分隔多份，每份可写 `名字=令牌`：共享令牌 + "改后图算生效版本" 合起来
#: 等于任何持令牌者都能产出生效图纸，多令牌至少能把一次生效追溯到席位。

TOKEN_ENV = "DRAWFORM_TOKEN"


#: 保留的完整审图工具页；新场景首页通过可选的 frontend 构建目录接入。
#: 令牌由使用者粘一次、存 sessionStorage —— 页面本身是空壳，放开它不降低安全性，
#: 边界划在 API 上（见 require_token 的豁免注释）。
PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>汽绘 DrawForm · 审图台</title>
<style>
 :root{--mat:#E8E9EB;--paper:#FFFFFF;--panel:#F7F8F9;--line:#E3E5E9;--line-strong:#CDD1D7;
  --ink:#1A1D21;--ink-2:#5A6270;--ink-3:#8A929E;--accent:#1E6FD9;--pass:#1B7F4B;
  --machine:#B26A00;--frozen:#B3261E;--outlib:#6B4FBB;--bar:44px;--side:340px}
 *{box-sizing:border-box} html,body{height:100%}
 body{margin:0;background:var(--mat);color:var(--ink);
  font:13px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
  font-variant-numeric:tabular-nums;display:flex;flex-direction:column;overflow:hidden}
 #top{height:var(--bar);flex:none;background:var(--panel);border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:14px;padding:0 14px}
 #top h1{font-size:16px;font-weight:600;white-space:nowrap;margin:0}
 #top .sep{width:1px;height:20px;background:var(--line-strong);flex:none}
 #part{color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 #state{flex:1;min-width:0;color:var(--ink-2);overflow-wrap:anywhere}
 .mono{font-family:ui-monospace,"SF Mono",Menlo,monospace}
 #main{flex:1;display:flex;min-height:0}
 #side{width:var(--side);flex:none;background:var(--panel);border-right:1px solid var(--line);
  overflow:auto;padding:0 14px 20px}
 /* 纸张的 width:100% 依赖可伸展的父宽；居中交给 #paper 的 margin。 */
 #stage{flex:1;min-width:0;overflow:auto;padding:16px;display:flex;flex-direction:column;
  align-items:stretch;justify-content:flex-start}
 #paper{background:var(--paper);box-shadow:0 1px 4px rgba(0,0,0,.22);width:100%;max-width:1180px;
  margin:0 auto;position:relative;flex:none}
 #paper svg{display:block;width:100%;height:auto;transform:scale(var(--z,1));transform-origin:0 0}
 #annots{position:absolute;inset:0;pointer-events:none}
 #annots .ring{position:absolute;border:1.5px solid var(--frozen);border-radius:3px;
  background:rgba(179,38,30,.06)}
 #annots .ring[data-kind="removed"]{border-style:dashed}
 #annots .tag{position:absolute;left:-1.5px;bottom:calc(100% + 2px);white-space:nowrap;
  font-size:11px;font-weight:600;background:var(--frozen);color:#fff;padding:2px 6px;border-radius:2px;
  box-shadow:0 1px 3px rgba(0,0,0,.25)}
 #zoomctl{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:10px;flex:none}
 #zoominfo,#zoom-hint{font-size:12px;color:var(--ink-2)}
 #stage:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
 .empty{padding:48px 24px;text-align:center;color:var(--ink-2)}
 .empty h2{color:var(--ink);font-size:16px;margin:0 0 8px}.empty p{margin:0 0 16px}
 section{border-top:1px solid var(--line)} section:first-of-type{border-top:none}
 section h2{font-size:13px;font-weight:600;margin:0}
 .section-toggle{font:inherit;font-weight:600;color:var(--ink-2);width:100%;border:0;background:none;
  padding:12px 0 10px;cursor:pointer;display:flex;align-items:center;gap:6px;text-align:left}
 .section-toggle .tri{color:var(--ink-3);font-size:10px;transition:transform .12s}
 section[data-open="0"] .tri{transform:rotate(-90deg)}
 section[data-open="0"] .body{display:none}.section-toggle .badge{margin-left:auto}
 .body{padding-bottom:14px} h3{font-size:13px;margin:14px 0 6px}
 .badge{font-size:12px;padding:2px 6px;border-radius:2px;white-space:nowrap;border:1px solid transparent}
 .b-pass{background:#E8F4EC;color:var(--pass);border-color:#BFE0CC}
 .b-machine{background:#FFF3E0;color:var(--machine);border-color:#F0D6A8}
 .b-frozen{background:#FBE9E7;color:var(--frozen);border-color:#EFC0BA}
 .b-outlib{background:#EFEAF9;color:var(--outlib);border-color:#D3C7EE}
 .b-ink{background:#EFF1F3;color:var(--ink-2);border-color:var(--line-strong)}
 .btn,.chip{font:inherit;height:28px;padding:0 11px;border-radius:3px;cursor:pointer;
  border:1px solid var(--line-strong);background:#fff;color:var(--ink);white-space:nowrap}
 .btn:hover,.chip:hover{background:#F0F2F4}
 .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
 .btn.primary:hover{background:#1A63C2}.btn[disabled]{opacity:.45;cursor:not-allowed}
 .btnrow{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}.btnrow .wide{flex:1}
 input[type=text],input[type=password],select,textarea{font:inherit;padding:4px 6px;
  border:1px solid var(--line-strong);border-radius:3px;background:#fff;color:var(--ink);width:100%}
 input[type=text],input[type=password],select{height:28px} textarea{resize:vertical}
 input:focus,select:focus,textarea:focus,button:focus-visible,a:focus-visible{
  outline:2px solid #BBD3F5;outline-offset:-1px;border-color:var(--accent)}
 label.field{display:block;margin:8px 0 4px;color:var(--ink-2)}
 input[type=file]{width:100%;font:inherit;margin:4px 0 6px}
 .hint{color:var(--ink-3);font-size:11px;margin:4px 0}.notice{color:var(--ink-2);overflow-wrap:anywhere}
 .dl{display:inline-flex;align-items:center;margin:3px 6px 3px 0;padding:3px 9px;
  border:1px solid var(--line-strong);border-radius:3px;text-decoration:none;color:var(--ink)}
 .assume{border-left:3px solid var(--machine);padding:6px 9px;margin:7px 0;background:#FFFAF2;
  border-radius:3px;overflow-wrap:anywhere}.assume b{display:block}.assume span{color:var(--ink-2)}
 .card{min-width:0;border:0;border-left:3px solid var(--line-strong);background:#fff;
  padding:6px 9px;margin:7px 0;border-radius:3px;overflow-wrap:anywhere}
 .card.t-machine{border-left-color:var(--machine);background:#FFFAF2}
 .card.t-outlib{border-left-color:var(--outlib);background:#F8F6FD}
 .card.t-frozen{border-left-color:var(--frozen);background:#FDF4F3}
 .card.t-pass{border-left-color:var(--pass);background:#F4FAF6}
 .card.t-ink{border-left-color:var(--line-strong);background:#fff}
 .card-head{display:flex;align-items:center;gap:6px;justify-content:space-between}
 .card-head b{font-size:13px;font-weight:600}
 .card legend{float:left;width:100%;padding:0;margin-bottom:4px;font-weight:600;
  display:flex;align-items:center;justify-content:space-between;gap:6px}
 .card legend+*{clear:both}.card p{margin:4px 0}.card .target{font-weight:600}
 .card .meta,.card .why{font-size:12px;color:var(--ink-2)}
 .card .warn{color:var(--machine);font-size:12px;font-weight:600}
 .options{max-height:190px;overflow:auto;margin:6px 0;overscroll-behavior:contain}
 .opt{display:flex;gap:8px;align-items:flex-start;padding:5px 6px;border-radius:3px;
  cursor:pointer;border:1px solid transparent}
 .opt:hover{background:#F0F2F4}.opt input{margin-top:3px;flex:none}
 .opt .o-main{font-size:13px}.opt .o-sub{color:var(--ink-3);font-size:12px;display:block}
 .opt .o-sub:empty{display:none}.card textarea{min-height:52px;font-size:12px}
 #question-actions{padding-top:8px}#question-actions .primary{width:100%}
 #reference-actions .primary{width:100%}.reference-preview{padding:12px;border-bottom:1px solid var(--line)}
 #drawing .reference-preview svg{width:100%;height:220px}.reference-preview figcaption{font-weight:600}
 #verbar{display:flex;gap:0;align-items:center;margin-bottom:10px;border:1px solid var(--line-strong);
  border-radius:3px;background:#fff;flex:none;flex-wrap:wrap;overflow:hidden}
 #verbar>button{font:inherit;padding:5px 12px;border:0;background:#fff;cursor:pointer;color:var(--ink-2);
  border-right:1px solid var(--line)}
 #verbar>button.on{background:var(--accent);color:#fff;font-weight:600}
 #verbar>button:disabled{opacity:.45;cursor:not-allowed}
 #verbar .vtag{padding:0 12px;color:var(--ink-3);font-size:12px;flex:1}
 #version-select{width:auto;max-width:180px;margin:4px 6px}
 #history-notice{padding:8px 10px;margin:0 0 10px;background:var(--panel);border-left:3px solid var(--machine)}
 #chips{display:flex;gap:5px;flex-wrap:wrap;margin:6px 0}.chip{font-size:12px;padding:0 6px}
 #qform{display:flex;gap:6px}#q{min-width:0;flex:1}
 #reasons label{display:flex;gap:6px;align-items:flex-start;margin:5px 0;font-size:12px}
 #reasons input{flex:none;margin-top:4px}#jobs{padding:0;list-style:none;margin:5px 0}
 #reasons .reason-blocking{border:1px solid var(--line-strong);border-left-width:3px;padding:5px}
 #reasons .reason-blocking:has(:checked){border-left-color:var(--frozen)}
 #blocking-warning{color:var(--frozen)}
 #jobs li{cursor:pointer;padding:3px 6px;border-radius:3px;font-size:12px;color:var(--ink-2)}
 #jobs button{width:100%;height:auto;min-height:28px;text-align:left;white-space:normal;overflow-wrap:anywhere}
 #jobs li:hover{background:#EFF1F3;color:var(--ink)}
 footer{padding:7px 14px;color:var(--ink-3);font-size:11px;border-top:1px solid var(--line);
  background:var(--panel);flex:none} [hidden]{display:none!important}
 @media(max-width:760px){
  #top{height:auto;min-height:var(--bar);flex-wrap:wrap;gap:5px 10px;padding:6px 12px}
  #top h1{font-size:14px}#state{flex-basis:55%;font-size:12px}#part{max-width:45%}
  #main{flex-direction:column}#side{width:100%;height:48%;border-right:0;border-bottom:1px solid var(--line)}
  #stage{min-height:220px;padding:12px}#verbar{overflow:visible}#version-select{max-width:150px}
  #verbar .vtag{order:1;flex-basis:100%;padding:4px 12px;border-top:1px solid var(--line)}
  footer{padding:5px 12px}
 }

 .workspace{width:min(1060px,100%);margin:16px auto 40px}.workspace h2{font-size:23px;margin:4px 0 6px}
 .workspace-lead{font-size:14px;color:var(--ink-2);margin:0 0 22px}
 .workspace-card{background:var(--paper);border:1px solid var(--line-strong);border-radius:6px;padding:20px;margin:14px 0}
 #connection-card.connected{display:flex;gap:16px;align-items:center;flex-wrap:wrap}#connection-card.connected h3{margin:0}#connection-card.connected #connection-state{flex:1;margin:0;min-width:210px}
 .workspace-card h3{margin:0 0 12px;font-size:16px}.workspace-card .btn{height:34px}
 .workspace-card input[type=password],.workspace-card input[type=search],.workspace-card select{height:36px}
 .workspace-card #token{max-width:520px}.workspace-card #up{max-width:620px}
 #connection-state{margin:8px 0;color:var(--ink-2)}.flow-steps{color:var(--ink-2);margin:14px 0}
 #job-tools{display:flex;gap:10px;flex-wrap:wrap}#job-search{flex:1;min-width:180px;font:inherit;border:1px solid var(--line-strong);border-radius:3px;padding:6px 10px}#job-filter{width:160px}
 #jobs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:16px}
 #jobs li{padding:0}#jobs button{padding:12px;text-align:left;min-height:80px;display:block;background:white}
 #jobs button[aria-current=page]{border:2px solid var(--accent)}.job-name{font-size:14px;color:var(--ink);display:block;font-weight:600}
 .job-meta{display:block;margin-top:5px;color:var(--ink-2);font-size:12px}.job-state{display:inline-block;margin-top:5px;font-size:12px}
 body[data-screen=workspace] #side{display:none}body[data-screen=workspace] #stage>:not(#workspace){display:none!important}
 body[data-screen=task] #workspace{display:none}#task-title{margin:0 0 8px;font-size:16px}#task-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
 #task-draft-status{font-size:12px;color:var(--ink-2)}#reference-nav{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
 #reference-nav button[aria-current=true]{border-color:var(--accent);color:var(--accent);font-weight:600}
 #reference-nav .answered{background:#E8F4EC}#reference-questions .options{max-height:none;overflow:visible}
 .reference-option{border-bottom:1px solid var(--line);padding:6px 0}.reference-option .btn{margin:3px 0 6px 26px;font-size:12px}
 .reference-option .plane-data{margin-left:26px;color:var(--ink-2);font-size:11px}#reference-preview-label{padding:8px 12px;background:var(--panel);margin:0}
 #reference-actions{position:sticky;bottom:-20px;background:var(--panel);padding:8px 0 20px;border-top:1px solid var(--line)}
 #draft-banner{border-left:3px solid var(--accent);background:#F1F6FE;padding:10px;margin-bottom:12px}
 #review-confirm{border:1px solid var(--line-strong);padding:12px;margin-top:12px;background:#fff}#review-summary{white-space:pre-line}
 #review-guidance{font-size:12px;color:var(--ink-2)}#effective-ack-label{display:flex;gap:8px;margin:10px 0;align-items:flex-start}
 #effective-ack{margin-top:5px}#rvBtns button[aria-pressed=true]{border-color:var(--accent);background:#E9F1FF;color:var(--accent)}
 .result-panel{padding:30px;max-width:800px;margin:auto}.result-panel h2{font-size:20px}.result-panel li{margin:10px 0}
 @media(max-width:760px){#reference-actions{position:static;bottom:auto;padding-bottom:8px}.workspace{margin:4px auto 20px}.workspace-card{padding:14px}#jobs{grid-template-columns:1fr}#workspace-link{margin-left:auto}#task-toolbar{align-items:flex-start;flex-wrap:wrap}body[data-screen=workspace] #stage{min-height:0}}
</style>
</head>
<body>
<header id="top"><h1>汽绘 DrawForm</h1><span class="sep"></span>
 <span id="part" class="mono">尚未载入图纸</span><span id="state" role="status" aria-live="polite"></span>
 <span id="version-label" class="badge b-ink">审图台</span><button id="workspace-link" class="btn" type="button">任务工作台</button></header>
<div id="main">
<aside id="side" aria-label="审图操作">
 <section id="question-section" data-open="1">
  <h2><button class="section-toggle" type="button" aria-expanded="true" aria-controls="questions-body">
   <span class="tri" aria-hidden="true">▾</span>需要你确认的<span id="question-count" class="badge b-outlib">待载入</span></button></h2>
  <div id="questions-body" class="body">
   <label class="field" for="who">操作人（回答与复核使用同一姓名）</label>
   <input id="who" type="text" autocomplete="name" placeholder="你的名字（自报，接 SSO 前不算身份凭证）">
   <p id="questions-context" class="notice">上传 STEP 后显示需要确认的问题。</p>
   <p id="questions-empty" class="notice" hidden>暂无待确认项</p><div id="questions"></div>
   <div id="reference-nav" aria-label="选择要查看的孔" hidden></div><div id="reference-questions" hidden></div>

   <div id="question-actions" hidden><p id="question-progress" class="notice" role="status" aria-live="polite"></p>
    <button id="submit-answers" class="btn primary" type="button" disabled>提交并重出图</button>
    <p class="hint">不确定时可逐项选“我不知道”，对应要求保留未注或占位状态。</p></div>
   <div id="reference-actions" hidden><p id="reference-progress" class="notice" role="status" aria-live="polite"></p>
    <button id="submit-references" class="btn primary" type="button" disabled>确认参考并计算图纸</button>
    <div class="btnrow"><button id="next-reference" class="btn" type="button">下一项未选孔</button><button id="reference-later" class="btn" type="button">保存草稿，稍后确认</button></div>
    <p class="hint">预览不代表选择。全部逐孔填写后才可提交；有“不知道”时将记录原因并停止出图。参考确认不等于制图审核。</p></div>
   <p id="answer-receipt" class="notice" role="status" aria-live="polite"></p></div>
 </section>
 <section id="assumption-section" data-open="1">
  <h2><button class="section-toggle" type="button" aria-expanded="true" aria-controls="assumptions-body">
   <span class="tri" aria-hidden="true">▾</span>出图说明</button></h2>
  <div id="assumptions-body" class="body"><p id="assumption-caption" class="hint">机器替你做的决定，复核前请逐项查看。</p>
   <div id="answer-records"></div><div id="assumptions"></div></div>
 </section>
 <section id="ask-section" data-open="0">
  <h2><button class="section-toggle" type="button" aria-expanded="false" aria-controls="ask-body" aria-keyshortcuts="Q">
   <span class="tri" aria-hidden="true">▾</span>问这张图</button></h2>
  <div id="ask-body" class="body"><div id="chips"></div><label class="field" for="q">关于当前图纸的问题</label>
   <form id="qform"><input id="q" type="text" placeholder="为什么这条尺寸从那个面量起？"><button class="btn">问</button></form>
   <div id="answers" aria-live="polite"></div></div>
 </section>
 <section id="review-section" data-open="0">
  <h2><button class="section-toggle" type="button" aria-expanded="false" aria-controls="review-body" aria-keyshortcuts="R">
   <span class="tri" aria-hidden="true">▾</span>复核这张图</button></h2>
  <div id="review-body" class="body"><p class="hint">复核记录使用上方填写的操作人姓名。</p>
   <button id="edit-who" class="btn" type="button">填写或修改姓名</button>
   <div id="rvBtns" class="btnrow"><button class="chip" data-d="accepted">直接用</button>
    <button class="chip" data-d="amended">改后采用</button><button class="chip" data-d="rejected">驳回</button></div>
   <div id="reasons">__REASONS__</div>
   <p id="blocking-warning" role="status" hidden>⚠ 数值级理由一旦记入，这张图就不能被标为直接用。</p>
   <label class="field" for="review-note">复核说明（不参与数值）</label>
   <textarea id="review-note" rows="2" aria-describedby="review-note-hint"></textarea>
   <p id="review-note-hint" class="hint">选择“以上都不是”时说明必填。</p>
   <p id="review-guidance" role="status"></p>
   <label id="effective-ack-label" hidden><input id="effective-ack" type="checkbox">我已下载并核对当前生效 DXF；知道画布仍显示引擎图。</label>
   <div id="review-confirm" hidden><b>核对复核摘要</b><p id="review-summary"></p>
    <div class="btnrow"><button id="confirm-review" class="btn primary" type="button" disabled>确认提交复核</button><button id="cancel-review" class="btn" type="button">取消</button></div>
    <p class="hint">提交后会留下复核记录；记录使用事实，不代替线下签署。</p></div>
   <div id="rvState" class="notice" aria-live="polite"></div>
   <h3>下载当前显示的图纸</h3><div id="downloads"></div>
   <button id="package-download" class="btn" type="button" disabled>下载交付包</button>
   <h3>回传改后图（生效版本）</h3>
   <form id="amend"><label class="field" for="amended-file">改后的 ASCII DXF</label>
    <p id="reference-amend-hint" class="hint" hidden>本图含已确认参考截面。回传DXF须保留参考定义与交点引注；需要更换参考时，请重新提交STEP。</p>
    <input id="amended-file" type="file" name="file" accept=".dxf">
    <button class="btn" type="submit">上传为生效版本</button></form><div id="diff" class="notice"></div></div>
 </section>
 <section id="task-section" data-open="0">
  <h2><button class="section-toggle" type="button" aria-expanded="false" aria-controls="task-body" aria-keyshortcuts="U">
   <span class="tri" aria-hidden="true">▾</span>任务</button></h2>
  <div id="task-body" class="body"><label class="field" for="token">访问令牌</label>
   <input id="token" type="password" placeholder="粘贴本机试点的访问令牌" autocomplete="off">
   <form id="up"><label class="field" for="step-file">STEP 零件模型</label>
    <input id="step-file" type="file" name="step" accept=".step,.stp">
    <label class="field" for="sheet-size">图纸幅面</label>
    <select id="sheet-size" name="sheet"><option value="A3">A3（默认）</option><option value="A2">A2（较大图幅，适合密集零件）</option></select>
    <p class="hint">本任务后续重出图保持所选幅面；尺寸字高保持，装不下时仍会说明原因。</p>
    <p class="hint">支持 STEP / STP，单个文件不超过 64 MB。上传后先检查模型，必要时请你确认参考。</p>
    <button class="btn primary" type="submit">上传并检查模型</button><p id="upload-status" role="status"></p></form>
   <button id="refresh-job" class="btn" type="button" disabled>刷新当前任务</button>
   <h3 id="legacy-jobs-title">历史任务</h3><ul id="jobs"></ul><button id="other-tasks" class="btn" type="button">上传模型 / 查看其他任务</button></div>
 </section>
</aside>
<main id="stage" aria-label="图纸画布" tabindex="0" aria-describedby="zoom-hint">
 <div id="workspace" class="workspace">
  <h2>任务工作台</h2><p class="workspace-lead">上传零件模型，确认需要补充的依据，再复核图纸。</p>
  <p class="flow-steps">1 连接服务　→　2 上传模型　→　3 确认与出图　→　4 复核与下载</p>
  <div id="connection-card" class="workspace-card"><h3>连接本机试点</h3>
   <p id="connection-state" role="status">首次使用需要访问令牌。可从本机试点目录的令牌文件复制，或向试点管理员获取。</p>
   <div id="connection-fields"><div id="connection-slot"></div><div class="btnrow"><button id="connect" class="btn primary" type="button">连接服务</button></div></div>
   <button id="change-token" class="btn" type="button" hidden>切换访问令牌</button></div>
  <div id="upload-card" class="workspace-card" hidden><h3>新建任务</h3><p><a href="/workbench/">用文字生成带孔板模型 → STEP → 工程图</a></p><div id="upload-slot"></div></div>
  <div id="tasks-card" class="workspace-card" hidden><h3>继续已有任务</h3>
   <div id="job-tools"><input id="job-search" type="search" aria-label="搜索文件名或任务号" placeholder="搜索文件名或任务号">
    <select id="job-filter" aria-label="筛选任务状态"><option value="">全部状态</option><option value="waiting_reference">待确认参考</option><option value="drawn">已出图</option><option value="refused">未能出图</option><option value="processing">处理中</option><option value="crashed">运行异常</option></select>
    <button id="refresh-jobs" class="btn" type="button">刷新列表</button></div><p id="job-list-status" role="status"></p><div id="task-list-slot"></div></div>
 </div>
 <div id="task-toolbar"><h2 id="task-title">当前任务</h2><span id="task-draft-status" role="status"></span><button id="go-review" class="btn" type="button" hidden>复核这张图</button></div>
 <div id="draft-banner" hidden><span id="draft-message"></span><button id="discard-draft" class="btn" type="button">放弃本页未提交内容</button></div>
 <div id="state-notice" class="card t-ink" role="status" aria-live="polite" hidden>
  <b id="notice-title"></b><p id="notice-detail"></p><div class="btnrow">
   <button class="btn" type="button" data-recovery="refresh">重新读取问题卡 / 刷新状态</button>
   <button class="btn" type="button" data-recovery="latest">下载最新生效 DXF</button>
   <button class="btn" type="button" data-recovery="amend">回传改后图</button>
   <button class="btn" type="button" data-recovery="reject">驳回并说明</button>
   <button class="btn" type="button" data-recovery="upload">重新提交 STEP</button>
   <button class="btn" type="button" data-recovery="token">填写访问令牌</button>
   <button class="btn" type="button" data-recovery="jobs">重新读取任务列表</button>
   <button class="btn" type="button" data-recovery="questions">返回填写处</button>
  </div></div>
 <div id="verbar" hidden>
 <button id="vb-before" type="button" aria-pressed="true">上一引擎版</button>
 <button id="vb-after" type="button" aria-pressed="false" disabled>当前引擎版</button>
 <span id="version-description" class="vtag">机器底稿</span>
 <label class="hint" for="version-select">引擎图面版本</label><select id="version-select"></select></div>
 <div id="history-notice" role="status" hidden>正在查看历史图面。返回当前版本后再回答、问答或复核。
  <button id="back-to-current" class="btn" type="button">返回当前版本</button></div>
 <div id="quick-downloads" aria-label="下载图纸"></div>
 <div id="paper"><div id="drawing"><div class="empty">
 <h2>上传 STEP，开始审图</h2><p>尺寸来自引擎计算，公差与基准由你确认。</p>
 <button id="start-upload" class="btn primary" type="button">开始上传</button></div></div>
 <div id="annots" role="region" aria-label="当前版本的文字变更提示" hidden></div></div>
 <div id="zoomctl" hidden><button class="btn" type="button" data-z="-">缩放 −</button>
  <button class="btn" type="button" data-z="fit">适合</button><button class="btn" type="button" data-z="1">100%</button>
  <button class="btn" type="button" data-z="+">缩放 +</button><span id="zoominfo" aria-live="polite"></span></div>
 <p id="zoom-hint" class="hint">缩放仅改变显示。图面获得焦点后可用方向键平移。</p></main>
</div>
<footer>未经制图审核 · 图纸由引擎渲染，交付使用前须经工程师复核。<span> U 上传 · Q 问图 · R 复核（输入时不触发）</span></footer>
<template id="question-card">
 <fieldset class="card t-outlib" data-question-card>
  <legend><span data-kind></span><span class="badge b-outlib" data-question-status>待你确认</span></legend>
  <p class="target" data-target></p><p class="meta">机器当前：<span data-claim></span></p>
  <p class="meta">依据：<span data-basis></span></p><p class="why">影响：<span data-impact></span></p>
  <p class="warn" data-truncated hidden>候选超过上限，已截断；不能据此认为没有其他平面或公差选择。</p>
  <div class="options" role="radiogroup" data-options></div>
  <label class="field">说明（不参与数值）<textarea data-free-note rows="2" maxlength="__NOTE_LIMIT__"></textarea></label>
  <div class="btnrow"><button class="btn" type="button" data-action="unknown">我不知道</button>
   <button class="btn wide" type="button" data-action="confirm" disabled>确认</button></div>
 </fieldset>
</template>
<template id="question-option"><label class="opt"><input type="radio">
 <span><span class="o-main" data-option-label></span><span class="o-sub" data-option-note></span></span></label></template>
<template id="assumption-card"><article class="card t-machine">
 <div class="card-head"><b data-fact-title>出图说明</b><span class="badge b-machine" data-fact-status>机器已决定</span></div>
 <p data-claim></p><p class="meta">依据：<span data-basis></span></p><p class="why">影响：<span data-impact></span></p>
 <p class="hint" data-unverified>未与集团真实图纸核对</p></article></template>
<template id="answer-record"><article class="card t-pass">
 <div class="card-head"><b>人工确认记录</b><span class="badge b-pass" data-answer-status>已确认</span></div>
 <p data-answer-target></p><p data-answer-choice></p><p class="meta" data-answer-note>说明（不参与数值）：<span></span></p>
 </article></template>
<template id="job-row"><li data-job="" data-state=""><button class="btn" type="button"></button></li></template>
<script>
const $=s=>document.querySelector(s);
function setOpen(section,open){section.dataset.open=open?"1":"0";
 section.querySelector(".section-toggle").setAttribute("aria-expanded",String(open))}
document.querySelectorAll(".section-toggle").forEach(button=>button.onclick=()=>{
 const section=button.closest("section");setOpen(section,section.dataset.open!=="1")});
$("#start-upload").onclick=()=>{goWorkspace();$(connected?"#step-file":"#token").focus()};
$("#edit-who").onclick=()=>{setOpen($("#question-section"),true);$("#who").focus()};
function readSession(key){try{return sessionStorage.getItem(key)}catch{return null}}
let token=readSession("dfToken")||""; $("#token").value=token;
let connected=false,sessionEpoch=0,sessionIdentity="",connectPromise=null,pendingToken="",jobsEpoch=0;
let draftPhase="loading";
let allJobs=[],reviewDecision="",reviewPending=null,activeReference=0,draftMuted=false,storageFailed=false,uploadBusy=false;
$("#token").onchange=()=>connect();
$("#token").onkeydown=event=>{if(event.key==="Enter"){event.preventDefault();connect()}};
const hdr=()=>({Authorization:"Bearer "+token});
const say=t=>{$("#state").textContent=t};
let jobId="";
let questionList=[], choices=new Map(), answerBusy=false;
let currentJob=null, versions=[], latestVersion=0, selectedVersion=0, viewingHistory=false;
let drawingReady=false, questionsReady=false, pageBusy=false, loadEpoch=0, previewEpoch=0;
let questionCatalog=[], versionCache=new Map(), draftSha="", isFrozen=false;
let referenceSnapshot=null, referenceChoices=new Map();
let zoomMode="fit", zoom=1;
const QUESTION_KIND={special_tolerance:"专门公差",datum_choice:"基准选择",unnoted_angle:"角度未注公差"};
function optionIssue(question,option){
 if(question.kind!=="datum_choice")return "";
 if(!["A","B","C"].includes(question.datum)||!["x","y","z"].includes(question.axis))return "缺少基准方向依据，请重新出图";
 if(option.id.startsWith("face-")&&!["x","y","z"].includes(option.axis))return "此平面缺少方向依据，暂不可选";
 if(option.axis&&option.axis!==question.axis)return `不适用于当前${question.axis.toUpperCase()}向基准，请选择${question.axis.toUpperCase()}=常数的平面`;
 return "";
}
function updateQuestionProgress(){
 const remaining=questionList.filter(q=>!choices.get(q.id)?.confirmed).length;
 $("#question-count").textContent=questionsReady?(questionList.length?`${remaining} 项待确认`:"0 项"):"待载入";
 $("#question-progress").textContent=remaining?`还有 ${remaining} 项未确认`:
  ($("#who").value.trim()?"各项已确认，可提交重出图":"各项已确认，请填写操作人姓名");
 if(isFrozen)$("#question-progress").textContent="存在数值级错误记录，当前任务不能提交答案";
 else if(currentJob&&headVersion(currentJob)>latestVersion)$("#question-progress").textContent="已有人工回传图；继续回答须重新提交 STEP";
 $("#submit-answers").disabled=!canAnswer()||!questionList.length||remaining>0||!$("#who").value.trim();
}
function syncQuestion(card){
 const state=choices.get(card.dataset.question), unknown=state.confirmed&&state.selected===null;
 const question=questionList.find(q=>q.id===card.dataset.question);
 const issue=state.selected==null?"":optionIssue(question,question.options.find(o=>o.id===state.selected));
 card.className="card "+(state.confirmed&&!unknown?"t-pass":"t-outlib");
 const badge=card.querySelector("[data-question-status]");
 badge.className="badge "+(state.confirmed&&!unknown?"b-pass":"b-outlib");
 badge.textContent=state.confirmed?((unknown?"不知道":"已确认")+(answerBusy?" · 重出图中":" · 待提交")):"待你确认";
 card.querySelectorAll("input[type=radio]").forEach(input=>{
  input.checked=input.value===state.selected;input.disabled=!canAnswer()||state.confirmed||!!optionIssue(question,question.options.find(o=>o.id===input.value))});
 const confirm=card.querySelector("[data-action=confirm]");
 confirm.textContent=state.confirmed?"撤回确认":"确认";
 confirm.disabled=!canAnswer()||(!state.confirmed&&(state.selected===undefined||!!issue));
 card.querySelector("[data-action=unknown]").disabled=!canAnswer();
 card.querySelector("textarea").disabled=!canAnswer();
 updateQuestionProgress();
}
function renderQuestions(questions){
 questionList=questions;questionsReady=true;choices=new Map();$("#questions").replaceChildren();
 $("#questions-empty").hidden=questions.length!==0;$("#questions-context").textContent="";
 $("#question-actions").hidden=questions.length===0;
 for(const q of questions){
  const card=$("#question-card").content.firstElementChild.cloneNode(true);
  card.dataset.question=q.id;choices.set(q.id,{selected:undefined,confirmed:false,note:""});
  card.querySelector("[data-kind]").textContent=QUESTION_KIND[q.kind];
  for(const field of ["target","claim","basis","impact"])card.querySelector(`[data-${field}]`).textContent=q[field];
  card.querySelector("[data-target]").id=`target-${q.id}`;
  card.querySelector("[data-options]").setAttribute("aria-labelledby",`target-${q.id}`);
  card.querySelector("[data-truncated]").hidden=!q.truncated;
  for(const option of q.options){
   const row=$("#question-option").content.firstElementChild.cloneNode(true), input=row.querySelector("input");
   input.name=q.id;input.value=option.id;
   row.querySelector("[data-option-label]").textContent=option.label;
   const issue=optionIssue(q,option);
   row.querySelector("[data-option-note]").textContent=[option.note,issue].filter(Boolean).join(" · ");
   if(issue){input.disabled=true;row.title=issue;row.dataset.incompatible="true"}
   card.querySelector("[data-options]").append(row);
  }
  $("#questions").append(card);syncQuestion(card);
 }
 updateQuestionProgress();
}
function answerPayload(){
 return questionList.map(q=>{
  const state=choices.get(q.id);
  if(!state?.confirmed||(state.selected!==null&&!q.options.some(o=>o.id===state.selected&&!optionIssue(q,o))))
   throw new Error("请逐项确认候选，或明确选择我不知道");
  return {question_id:q.id,option_id:state.selected,free_note:state.note};
 });
}
$("#questions").addEventListener("change",e=>{
 if(!e.target.matches("input[type=radio]"))return;
 const card=e.target.closest("[data-question-card]"), q=questionList.find(q=>q.id===card.dataset.question);
 if(!q.options.some(o=>o.id===e.target.value&&!optionIssue(q,o)))return;
 const state=choices.get(q.id);state.selected=e.target.value;state.confirmed=false;syncQuestion(card);
});
$("#questions").addEventListener("input",e=>{
 if(e.target.matches("textarea"))choices.get(e.target.closest("[data-question-card]").dataset.question).note=e.target.value;
});
$("#questions").addEventListener("click",e=>{
 const button=e.target.closest("button[data-action]");if(!button||!canAnswer())return;
 const card=button.closest("[data-question-card]"), state=choices.get(card.dataset.question);
 if(button.dataset.action==="unknown"){state.selected=null;state.confirmed=true}
 else if(state.confirmed){state.confirmed=false;state.selected=undefined}
 else if(state.selected!==undefined){const q=questionList.find(q=>q.id===card.dataset.question);if(!optionIssue(q,q.options.find(o=>o.id===state.selected)))state.confirmed=true}
 syncQuestion(card);
});
$("#who").addEventListener("input",refreshControls);
function factCard(fact){
 const card=$("#assumption-card").content.firstElementChild.cloneNode(true), id=fact.id||fact.fact_id;
 const unknown=id==="human-unknown"||id==="no-answer", unchecked=id==="title-block-unverified";
 const known=fact.checked===true, tone=unknown?"outlib":known?"pass":unchecked?"ink":"machine";
 card.className=`card t-${tone}`;
 const badge=card.querySelector("[data-fact-status]");badge.className=`badge b-${tone}`;
 badge.textContent=unknown?"不知道":known?"知道":unchecked?"未核对":"机器已决定";
 const titles={"datum-face":"基准依据","general-tolerance":"公差策略","open-chains":"尺寸链",
  "views-fixed":"视图选择","title-block-unverified":"标题栏核对","human-unknown":"人工未知记录","no-answer":"没有可核查依据"};
 card.querySelector("[data-fact-title]").textContent=titles[id]||"出图说明";
 for(const field of ["claim","basis","impact"])card.querySelector(`[data-${field}]`).textContent=fact[field]||"";
 card.querySelector("[data-unverified]").hidden=known;return card;
}
function renderAssumptions(doc,catalog=[]){
 $("#assumptions").replaceChildren(...(doc.assumptions||[]).map(factCard));
 $("#answer-records").replaceChildren();
 for(const answer of doc.answers||[]){
  const card=$("#answer-record").content.firstElementChild.cloneNode(true), unknown=answer.chosen_option_id===null;
  const question=catalog.find(q=>q.id===answer.question_id), option=question?.options.find(o=>o.id===answer.chosen_option_id);
  card.className="card "+(unknown?"t-outlib":"t-pass");
  const badge=card.querySelector("[data-answer-status]");badge.className="badge "+(unknown?"b-outlib":"b-pass");
  badge.textContent=unknown?"不知道 · 已记录":"已确认";
  card.querySelector("[data-answer-target]").textContent=question?.target||`问题 ${answer.question_id}`;
  card.querySelector("[data-answer-choice]").textContent=unknown?"我不知道，保持未注或占位状态":`选择：${option?.label||answer.chosen_option_id}`;
  card.querySelector("[data-answer-note]").hidden=!answer.note;
  card.querySelector("[data-answer-note] span").textContent=answer.note||"";
  $("#answer-records").append(card);
 }
}
const FORMATS=["dxf","svg","pdf","json"];
const STATE={queued:"排队中，前面还有任务在运行",running:"正在出图…",drawn:"已出图",refused:"未能出图",crashed:"出图过程中出错",waiting_reference:"需要先确认斜孔参考"};
const CHIPS=["这些尺寸从哪个面量起？","这张图的公差是多少？","为什么有的尺寸没画出来？","标题栏按哪个标准画的？"];
const POLL_INTERVAL=600, SLOW_TASK_AFTER=90000;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const sha256=async buf=>{const hash=await crypto.subtle.digest("SHA-256",buf);
 return [...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,"0")).join("")};
function canInteract(){return connected&&draftPhase==="ready"&&drawingReady&&questionsReady&&currentJob?.state==="drawn"&&!answerBusy&&!pageBusy&&!viewingHistory}
function canAnswer(){return canInteract()&&!isFrozen&&headVersion(currentJob)<=latestVersion}
function localAnswers(){return referenceChoices.size>0||[...choices.values()].some(s=>s.selected!==undefined||s.confirmed||s.note)}
function reviewProblem(){
 if(!canInteract())return "先打开当前图纸，等待读取完毕后再复核。";
 if(localAnswers())return "还有未提交的选择，请先提交，或明确放弃草稿后再复核当前图纸。";
 if(!$("#who").value.trim())return "请先填写上方的操作人姓名。";
 const selected=[...document.querySelectorAll('#reasons input:checked')];
 if(reviewDecision==="accepted"&&(isFrozen||selected.length))return "已勾选问题，不能直接采用。请改后采用、驳回，或自行核对并取消问题勾选。";
 if(["amended","rejected"].includes(reviewDecision)&&!selected.length)return "请至少选择一条原因。";
 if(selected.some(i=>i.dataset.noteRequired==="true")&&!$("#review-note").value.trim())return "选择了“以上都不是”，请填写复核说明。";
 if(currentJob&&headVersion(currentJob)>latestVersion&&!$("#effective-ack").checked)return "请下载并核对生效 DXF，然后勾选核对确认。画布仍是引擎图。";
 return "";
}
function updateReviewControls(){
 const selected=[...document.querySelectorAll('#reasons input:checked')];
 $("#review-note").required=selected.some(i=>i.dataset.noteRequired==="true");
 $("#blocking-warning").hidden=!selected.some(i=>i.dataset.group==="A");
 for(const button of document.querySelectorAll("#rvBtns button")){
  button.disabled=!canInteract()||!!reviewPending||(button.dataset.d==="accepted"&&(isFrozen||selected.length>0));
  button.setAttribute("aria-pressed",String(button.dataset.d===reviewDecision));
 }
 for(const input of document.querySelectorAll("#reasons input,#review-note,#effective-ack"))input.disabled=!!reviewPending||!canInteract();
 $("#effective-ack-label").hidden=!currentJob||headVersion(currentJob)<=latestVersion;
 const problem=reviewProblem();$("#review-guidance").textContent=problem||"选择结论后先核对摘要，再点击确认提交。";
 $("#confirm-review").disabled=!canInteract()||!reviewDecision||(!reviewPending&&!!problem);
 $("#confirm-review").textContent=reviewPending?"核对结果 / 重试同一提交":"确认提交复核";
 $("#cancel-review").disabled=!!reviewPending;
 if(reviewDecision)reviewSummary();
}
function headVersion(job){return Math.max(0,...Object.keys(job.files||{}).map(key=>{
 const match=key.match(/^amended-([0-9]+)$/);return match?Number(match[1]):0}))}
function refreshControls(){
 $("#sheet-size").disabled=!connected||uploadBusy;
 $("#go-review").hidden=currentJob?.state!=="drawn";$("#go-review").disabled=!canInteract();
 for(const card of document.querySelectorAll("[data-question-card]"))syncQuestion(card);
 updateQuestionProgress();
 $("#submit-answers").textContent=answerBusy?"正在重出图…":"提交并重出图";
 $("#question-actions").hidden=!questionList.length||viewingHistory||!questionsReady;
 $("#questions").hidden=viewingHistory;
 $("#questions-empty").hidden=!questionsReady||viewingHistory||questionList.length!==0;
 $("#history-notice").hidden=!viewingHistory;
 $("#who").disabled=answerBusy||pageBusy||!!reviewPending;
 for(const button of document.querySelectorAll("#rvBtns button,#amend button,#qform button,#chips button"))button.disabled=!canInteract();
 updateReviewControls();
 $("#amend button").disabled=!canInteract()||!$("#who").value.trim();
 $("#package-download").disabled=!canInteract()||!currentJob||headVersion(currentJob)===0;
 $("#refresh-job").disabled=!jobId||pageBusy||answerBusy;
 $("#up button").disabled=!connected||uploadBusy;
 $("#version-select").disabled=pageBusy||answerBusy;
 $("#vb-before").disabled=pageBusy||answerBusy||!drawingReady;
 $("#vb-after").disabled=pageBusy||answerBusy||latestVersion===0;
 for(const button of document.querySelectorAll("[data-recovery]"))button.disabled=pageBusy||answerBusy;
 for(const button of document.querySelectorAll("#zoomctl button"))button.disabled=pageBusy||answerBusy;
 updateReferenceControls();
}

function referenceNode(tag,text="",className=""){
 const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;
}
function updateReferenceControls(){
 const active=referenceSnapshot!==null;
 if(active)$("#question-count").textContent=`${referenceSnapshot.questions.length} 项图前确认`;
 $("#reference-actions").hidden=!active;
 $("#submit-references").disabled=!connected||!active||pageBusy||answerBusy||draftPhase!=="ready"||!$("#who").value.trim()
  ||referenceChoices.size!==referenceSnapshot.questions.length;
 $("#submit-references").textContent=answerBusy?"正在核验参考…":"确认参考并计算图纸";
 $("#reference-progress").textContent=active?`已选择 ${referenceChoices.size}/${referenceSnapshot.questions.length} 项。`+
  (!$("#who").value.trim()?"请先填写上方的操作人姓名。":referenceChoices.size<referenceSnapshot.questions.length?"可继续选择，或保存草稿稍后确认。":"可提交参考选择。"):
  "";
 for(const input of document.querySelectorAll("#reference-questions input"))input.disabled=!connected||pageBusy||answerBusy||draftPhase!=="ready";
}
function assertReferenceSnapshot(body){
 const s=body.snapshot;
 if(!s||typeof s.basis_sha!=="string"||!Array.isArray(s.questions)||!s.questions.length
  ||!s.planes||typeof s.planes!=="object"||!Array.isArray(s.preview)||s.preview.length!==3
  ||new Set(s.questions.map(q=>q.id)).size!==s.questions.length
  ||s.questions.some(q=>typeof q.id!=="string"||!Array.isArray(q.options)||!q.options.length||q.options.length>64
   ||q.allow_unknown!==true||!Array.isArray(q.axis_ends)||q.axis_ends.length!==2
   ||q.options.some(o=>typeof o.id!=="string"||!s.planes[o.plane_id])))
  throw new Error("参考候选依据不完整，请重新提交STEP；尚未生成交付图");
 return s;
}
function referencePreview(question,option=null){
 const s=referenceSnapshot;if(!s)return;
 const ns="http://www.w3.org/2000/svg",root=$("#drawing");root.replaceChildren();
 root.append(referenceNode("p",`${question.target} · 只读源几何预览；蓝线为目标孔轴，紫线为正在预览的参考面，圆圈为交点。预览用于确认参考，尚未生成加工图。`,"notice"));
 const project=(p,basis)=>{if(!Array.isArray(p)||p.length!==3||!p.every(Number.isFinite))throw new Error("参考预览坐标无效");
  return basis.map(v=>v.reduce((sum,n,i)=>sum+n*p[i],0))};
 for(const view of s.preview){
  if(!Array.isArray(view.basis)||view.basis.length!==2||view.basis.some(v=>!Array.isArray(v)||v.length!==3||!v.every(Number.isFinite)))
   throw new Error("参考预览方向依据无效");
  const face=option?s.planes[option.plane_id]:null;
  const axis=question.axis_ends.map(p=>project(p,view.basis));
  const plane=face?face.paths.map(path=>path.map(p=>project(p,view.basis))):[];
  const all=[...view.visible,...view.hidden,...plane,axis].flat();
  if(!all.length||all.some(p=>!Array.isArray(p)||p.length!==2||!p.every(Number.isFinite)))throw new Error("参考预览缺少有效源几何");
  const [x0,y0,x1,y1]=all.reduce((b,p)=>[Math.min(b[0],p[0]),Math.min(b[1],p[1]),Math.max(b[2],p[0]),Math.max(b[3],p[1])],[Infinity,Infinity,-Infinity,-Infinity]);
  const pad=Math.max(1,(x1-x0)/30,(y1-y0)/30),svg=document.createElementNS(ns,"svg");
  svg.setAttribute("viewBox",`${x0-pad} ${-y1-pad} ${x1-x0+2*pad} ${y1-y0+2*pad}`);
  svg.setAttribute("role","img");svg.setAttribute("aria-label",`${view.name}源几何、目标孔轴与参考关系`);
  const stroke=(paths,color,dashed=false,width=1)=>{for(const path of paths){const line=document.createElementNS(ns,"polyline");
   line.setAttribute("points",path.map(p=>`${p[0]},${-p[1]}`).join(" "));line.setAttribute("fill","none");line.setAttribute("stroke",color);
   line.setAttribute("stroke-width",width);line.setAttribute("vector-effect","non-scaling-stroke");if(dashed)line.setAttribute("stroke-dasharray","4 3");svg.append(line)}};
  stroke(view.visible,"var(--ink)");stroke(view.hidden,"var(--ink-3)",true);stroke(plane,"var(--outlib)",false,2);stroke([axis],"var(--accent)",true,2);
  if(option){const point=project(option.point,view.basis),dot=document.createElementNS(ns,"circle");
   dot.setAttribute("cx",point[0]);dot.setAttribute("cy",-point[1]);dot.setAttribute("r",pad/3);dot.setAttribute("fill","none");dot.setAttribute("stroke","var(--accent)");
   dot.setAttribute("stroke-width","2");dot.setAttribute("vector-effect","non-scaling-stroke");svg.append(dot)}
  const figure=referenceNode("figure","","reference-preview");figure.append(referenceNode("figcaption",{front:"主视源几何",top:"俯视源几何",left:"左视源几何"}[view.name]||view.name),svg);root.append(figure);
 }
}
function focusReference(index,option){
 if(!referenceSnapshot)return;activeReference=index;
 const question=referenceSnapshot.questions[index];if(!question)return;
 for(const card of document.querySelectorAll("[data-reference-question]"))card.hidden=card.dataset.referenceQuestion!==question.id;
 for(const button of document.querySelectorAll("#reference-nav button"))button.setAttribute("aria-current",String(Number(button.dataset.index)===index));
 referencePreview(question,option||null);
 const label=referenceNode("p",`正在查看孔 ${index+1}/${referenceSnapshot.questions.length} · ${option?"预览参考面，不会改变已选项":"目标孔轴"}`,"notice");label.id="reference-preview-label";
 $("#drawing").prepend(label);
}
function syncReferenceNavigation(){
 for(const button of document.querySelectorAll("#reference-nav button")){
  const q=referenceSnapshot.questions[Number(button.dataset.index)],chosen=referenceChoices.has(q.id);
  button.classList.toggle("answered",chosen&&referenceChoices.get(q.id)!==null);button.classList.toggle("b-outlib",chosen&&referenceChoices.get(q.id)===null);button.textContent=`${Number(button.dataset.index)+1}${chosen?(referenceChoices.get(q.id)===null?" ?":" ✓"):""}`;
  button.setAttribute("aria-label",`孔 ${Number(button.dataset.index)+1}，${chosen?(referenceChoices.get(q.id)===null?"已选不知道":"已选择参考"):"未选择"}`);
 }
 updateReferenceControls();
}
function renderReferences(snapshot){
 referenceSnapshot=snapshot;referenceChoices.clear();questionsReady=false;questionList=[];
 $("#questions").replaceChildren();$("#questions-context").textContent="先预览目标孔与候选面，再逐孔选择参考。未提交前没有交付图。";
 $("#question-count").textContent=String(snapshot.questions.length);$("#version-label").textContent="待确认参考";
 $("#reference-nav").hidden=false;$("#reference-nav").replaceChildren(...snapshot.questions.map((q,index)=>{
  const button=referenceNode("button",String(index+1),"btn");button.type="button";button.dataset.index=String(index);button.onclick=()=>focusReference(index);return button;
 }));
 const cards=snapshot.questions.map((q,index)=>{
  const card=referenceNode("fieldset","","card t-outlib");card.dataset.referenceQuestion=q.id;
  card.append(referenceNode("legend",`孔 ${index+1} · ${q.target}`),referenceNode("p",`定位方向：${q.axes.join(" / ").toUpperCase()}。先点击“预览”比较，再勾选所需参考。`,"meta"));
  const options=referenceNode("div","","options");
  const add=(option,i)=>{const row=referenceNode("div","","reference-option"),label=referenceNode("label","","opt"),radio=document.createElement("input");radio.type="radio";radio.name=`reference-${q.id}`;
   radio.value=option?option.id:"unknown";
   label.append(radio,referenceNode("span",option?`参考面 ${i+1} · ${option.on_patch?"交点在有限面片上":"延拓平面（交点在面片外或孔洞内）"}`:"我不知道（提交后停止出图）"));
   radio.onchange=()=>{referenceChoices.set(q.id,option?option.id:null);syncReferenceNavigation();saveDraft()};row.append(label);
   if(option){const plane=snapshot.planes[option.plane_id],preview=referenceNode("button",`预览参考面 ${i+1}`,"btn");preview.type="button";preview.onclick=()=>focusReference(index,option);row.append(preview);
    if(Array.isArray(plane.normal)&&Number.isFinite(plane.constant))row.append(referenceNode("p",`源平面：${plane.normal.map((n,k)=>`${Number(n.toFixed(3))}${"xyz"[k]}`).join(" + ")} = ${Number(plane.constant.toFixed(3))} mm（显示近似值）`,"plane-data"));
   }options.append(row)};
  q.options.forEach(add);add(null,-1);card.append(options);return card;
 });
 $("#reference-questions").replaceChildren(...cards);$("#reference-questions").hidden=false;
 activeReference=0;focusReference(0);syncReferenceNavigation();say("待确认参考 · 尚未生成交付图");setOpen($("#question-section"),true);
}
$("#who").addEventListener("input",updateReferenceControls);
$("#submit-references").onclick=async()=>{
 if($("#submit-references").disabled||!referenceSnapshot)return;
 const id=jobId,epoch=++loadEpoch,snapshot=referenceSnapshot;
 const answers=snapshot.questions.map(q=>({question_id:q.id,option_id:referenceChoices.get(q.id)}));
 answerBusy=true;refreshControls();$("#answer-receipt").textContent="正在提交参考选择；该操作不代表审图采纳。";
 try{
  await api(`/jobs/${id}/reference-answers`,{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({reviewer:$("#who").value.trim(),basis_sha:snapshot.basis_sha,answers})});
  const done=await poll(id,epoch);if(!done||epoch!==loadEpoch)return;
  clearDraft();answerBusy=false;await show(id);await loadJobs();
 }catch(error){if(epoch===loadEpoch){
   saveDraft();handleError(error);$("#answer-receipt").textContent+=" 已保留本页参考草稿；状态核对前请勿另建任务重复提交。";}}
 finally{if(epoch===loadEpoch){answerBusy=false;refreshControls()}}
};
$("#reasons").addEventListener("change",updateReviewControls);
$("#review-note").addEventListener("input",updateReviewControls);
function applyZoom(){
 const svg=$("#drawing svg");if(!svg||!drawingReady)return;
 const box=svg.viewBox.baseVal;if(!box.width||!box.height)return;
 const stage=$("#stage"), padding=parseFloat(getComputedStyle(stage).paddingLeft)*2;
 const width=Math.min(1180,stage.clientWidth-padding),height=width*box.height/box.width;
 if(zoomMode==="fit"){
  const controls=["task-toolbar","quick-downloads","draft-banner","verbar","history-notice","state-notice","zoomctl","zoom-hint"].reduce((n,id)=>n+$("#"+id).getBoundingClientRect().height,0);
  zoom=Math.min(1,Math.max(100,stage.clientHeight-controls-padding-48)/height);
 }
 $("#paper").style.width=`${width*zoom}px`;$("#paper").style.maxWidth="none";
 $("#drawing").style.height=`${height*zoom}px`;svg.style.width=`${width}px`;svg.style.setProperty("--z",zoom);
 $("#zoominfo").textContent=`显示 ${Math.round(zoom*100)}%`;
}
$("#zoomctl").addEventListener("click",event=>{
 const action=event.target.closest("[data-z]")?.dataset.z;if(!action)return;
 zoomMode=action==="fit"?"fit":"manual";
 if(action==="1")zoom=1;
 else if(action!=="fit")zoom=Math.max(.25,Math.min(3,zoom+(action==="+"?.25:-.25)));
 applyZoom();
});
window.addEventListener("resize",applyZoom);
$("#stage").addEventListener("keydown",event=>{
 if(event.target!==$("#stage"))return;
 const pan={ArrowLeft:[-80,0],ArrowRight:[80,0],ArrowUp:[0,-80],ArrowDown:[0,80]}[event.key];
 if(pan){event.preventDefault();$("#stage").scrollBy(...pan)}
});
document.addEventListener("keydown",event=>{
 if(event.isComposing||event.ctrlKey||event.metaKey||event.altKey||event.target.closest("input,textarea,select,[contenteditable]"))return;
 const target={u:["task-section","step-file"],q:["ask-section","q"],r:["review-section",null]}[event.key.toLowerCase()];
 if(!target)return;event.preventDefault();
 if(event.key.toLowerCase()==="u"){goWorkspace();$(connected?"#step-file":"#token").focus();return}
 if(document.body.dataset.screen==="workspace"){say("请先选择已出图的任务，再问图或复核。");return}
 setOpen($("#"+target[0]),true);
 (target[1]?$("#"+target[1]):$("#review-section .section-toggle")).focus();
});
async function api(path,options={},read="json"){
 const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),15000);
 try{
  const response=await fetch(path,{...options,headers:{...hdr(),...(options.headers||{})},signal:controller.signal});
  if(!response.ok){const body=await response.json().catch(()=>({}));
   throw Object.assign(new Error(body.error||`接口返回 HTTP ${response.status}`),{status:response.status,body})}
  return await response[read]();
 }catch(error){if(error.name==="AbortError")throw Object.assign(new Error("请求未及时返回，请刷新任务状态核对后再操作"),{code:"request_timeout"});throw error}
 finally{clearTimeout(timer)}
}
function clearNotice(){$("#state-notice").hidden=true}
function showNotice(title,detail,actions,frozen=false){
 const notice=$("#state-notice");notice.className="card "+(frozen?"t-frozen":"t-ink");notice.hidden=false;
 $("#notice-title").textContent=title;$("#notice-detail").textContent=detail;
 for(const button of notice.querySelectorAll("[data-recovery]"))button.hidden=!actions.includes(button.dataset.recovery);
 say(title);
}
function handleError(error,target=$("#answer-receipt")){
 const body=error.body||{},reason=body.reason;
 let title="暂时无法连接服务",detail=error.message,actions=jobId?["refresh"]:["jobs"],frozen=false;
 if(error.status===401){title="连接已失效，请重新连接";detail="当前草稿已保留。返回工作台验证访问令牌后，可继续原任务。";actions=["token"];connected=false;$("#connection-state").textContent="连接已失效，请重新验证令牌。草稿仍保留。";$("#connection-fields").hidden=false;$("#change-token").hidden=true;$("#connection-card").classList.remove("connected")}
 else if(error.status===400){title="请检查填写内容";detail=error.message+"。当前选择已保留，请修改后再提交。";actions=["questions"]}
 else if(error.status===413){title="文件过大";detail="单个文件上限为 64 MB，请选择较小的 STEP 或 DXF。";actions=["upload"]}
 else if(error.status===404){title="任务或文件不存在";detail="请重新读取任务列表，确认任务仍在服务中。";actions=["jobs"]}
 else if(error.status===409){
  if(body.frozen||reason==="frozen"){
   isFrozen=true;frozen=true;title="数值级错误：这张图已冻结";
   detail="不能标为直接用或提交答案。请改好后回传生效版本，或选驳回并说明问题。";actions=["amend","reject","latest"];
  }else if(reason==="reference_definition_changed"){
   title="参考定义已改变，本次回传未生效";
   detail="请保留原图的参考方程、交点引注及显示属性后再回传；若要更换参考，请重新提交STEP。当前文件选择和原版本均保留。";actions=["amend","latest"];
  }else if(reason==="stale_base"||Object.hasOwn(body,"head")){
   title="图纸版本已变化，请读取最新版";
   detail=(body.error||"当前回答或回传图所依据的版本已变。")+" 请重新读取问题卡；回传改后图前先下载最新版。";actions=["refresh","latest","upload"];questionsReady=false;
  }else if(["stale_reference","reference_conflict"].includes(reason)){
   draftPhase="stale";title="参考依据已变化，请刷新任务";detail=error.message+"。本地旧草稿保留，刷新核对后再继续。";actions=["refresh","jobs"];
   $("#draft-banner").hidden=false;$("#draft-message").textContent=detail;
  }else if(["queued","running"].includes(body.state)){
   draftPhase="readonly";
   title=STATE[body.state];detail="当前任务仍在处理，请等待结果后再回答或复核。";actions=["refresh"];questionsReady=false;
  }else if(body.state==="refused"){
   title="被交付门拦下了";detail=[...(body.failed||[]),...(body.reasons||[])].join("；")||error.message;
   actions=["jobs","upload"];questionsReady=false;
  }else if(body.state==="crashed"){
   title="出图过程中出错";detail=(body.error||error.message)+"。请重新提交；若重复出现，请保留任务号交给开发者排查。";
   actions=["jobs","upload"];questionsReady=false;
  }else{title="当前任务没有可读取的出图依据";detail=error.message;actions=["refresh","upload"];questionsReady=false}
 }else if(error.code==="request_timeout"){
  title="尚未收到处理结果";detail=error.message;actions=jobId?["refresh"]:["jobs"];
 }
 target.textContent=title+"："+detail;showNotice(title,detail,actions,frozen);refreshControls();
}
$("#state-notice").addEventListener("click",async event=>{
 const action=event.target.closest("[data-recovery]")?.dataset.recovery;if(!action)return;
 if(action==="refresh"){if(jobId)show(jobId);return}
 if(action==="latest"){
  const id=jobId,epoch=loadEpoch;
  try{const job=await api(`/jobs/${id}`);if(id!==jobId||epoch!==loadEpoch)return;await save(headVersion(job)?`/jobs/${id}/effective`:job.files.dxf,`${id}-最新生效.dxf`)}
  catch(error){if(id===jobId&&epoch===loadEpoch)handleError(error)}return;
 }
 if(action==="amend"||action==="reject"){
  setOpen($("#review-section"),true);$(action==="amend"?"#amended-file":"#reasons input").focus();return;
 }
 if(action==="questions"){setOpen($("#question-section"),true);$("#who").focus();return}
 setOpen($("#task-section"),true);
 if(action==="jobs"){goWorkspace();loadJobs()}
 else{goWorkspace();if(action==="token")$("#connection-fields").hidden=false;$(action==="token"?"#token":"#step-file").focus()}
});
function assertQuestions(body){
 if(!Array.isArray(body.questions)||new Set(body.questions.map(q=>q.id)).size!==body.questions.length
  ||body.questions.some(q=>!QUESTION_KIND[q.kind]||!Array.isArray(q.options)||!q.options.length||q.allow_unknown!==true))
  throw new Error("问题清单不完整，请重新出图；不能将它视为没有问题");
 return body.questions;
}
async function poll(id,epoch){
 const started=Date.now();let slow=false;
 while(epoch===loadEpoch){
  const status=await api(`/jobs/${id}?detail=1`);if(epoch!==loadEpoch)return null;
  if(["drawn","refused","crashed","waiting_reference"].includes(status.state)){if(slow)clearNotice();say(STATE[status.state]||status.state);return status}
  const seconds=Math.floor((Date.now()-started)/1000);
  if(!slow&&Date.now()-started>=SLOW_TASK_AFTER){
   slow=true;showNotice("任务仍在处理",`已等待 ${seconds} 秒。复杂零件耗时可能较长，本页会在任务结束后自动更新。`,[]);
  }
  say(`${STATE[status.state]||status.state} · 已等待 ${seconds} 秒`);
  await sleep(slow?2000:POLL_INTERVAL);
 }
 return null;
}
function engineVersions(job){
 const root=Object.fromEntries(FORMATS.map(kind=>[kind,`/jobs/${job.job_id}/files/${kind}`]));
 const result=[{number:0,files:root}];
 for(const key of Object.keys(job.files)){
  const match=key.match(/^answer-([0-9]+)-svg$/);if(!match)continue;
  const number=Number(match[1]), files={dxf:job.files[`amended-${number}`]};
  for(const kind of ["svg","pdf","json"])files[kind]=job.files[`answer-${number}-${kind}`];
  if(FORMATS.every(kind=>files[kind]))result.push({number,files});
 }
 return result.sort((a,b)=>a.number-b.number);
}
function downloadLink(url,name,label){const link=document.createElement("a");link.className="dl";link.href=url;link.download=name;link.textContent=label;return link}
function renderAnnotations(doc){
 const overlay=$("#annots");overlay.replaceChildren();overlay.hidden=true;
 const annotations=doc.annotations??[];
 if(!Array.isArray(annotations))throw new Error("文字变更定位信息无效，请重新读取图纸");
 if(!annotations.length)return;
 const view=$("#drawing svg").viewBox.baseVal;
 const names={changed:"文字更新",moved:"文字位置变化",added:"新增文字",removed:"移除文字"};
 if(!view.width||!view.height||!Number.isInteger(doc.annotation_base_version)||doc.annotation_base_version<0
  ||annotations.some(a=>!Object.hasOwn(names,a.kind)||!Array.isArray(a.box)||a.box.length!==4
   ||!a.box.every(Number.isFinite)||a.box[2]<=0||a.box[3]<=0
   ||(a.before!==null&&typeof a.before!=="string")||(a.after!==null&&typeof a.after!=="string")))
  throw new Error("文字变更缺少有效的引擎位置依据，请重新读取图纸");
 overlay.setAttribute("aria-label",`相对${doc.annotation_base_version?`第 ${doc.annotation_base_version} 版`:"机器底稿"}的文字变更提示`);
 for(const item of annotations){
  const ring=document.createElement("div");ring.className="ring";ring.dataset.kind=item.kind;
  // 引擎提供 SVG 坐标；这里只换成显示百分比，缩放由纸张容器统一完成。
  const [x,y,width,height]=item.box;
  ring.style.left=`${x/view.width*100}%`;ring.style.top=`${y/view.height*100}%`;
  ring.style.width=`${width/view.width*100}%`;ring.style.height=`${height/view.height*100}%`;
  const description=`${names[item.kind]}：${item.before??"—"} → ${item.after??"—"}`;
  ring.setAttribute("role","note");ring.setAttribute("aria-label",description);ring.title=description;
  const tag=document.createElement("span");tag.className="tag";tag.textContent=names[item.kind];
  ring.append(tag);overlay.append(ring);
 }
 overlay.hidden=false;
}
function paintVersion(version,svg,doc){
 if(!svg.trim().startsWith("<svg"))throw new Error("图面文件不是有效的引擎 SVG，请重新出图");
 $("#drawing").innerHTML=svg;selectedVersion=version.number;viewingHistory=selectedVersion!==latestVersion;drawingReady=true;
 renderAnnotations(doc);
 $("#version-select").value=String(selectedVersion);
 const before=versions.length>1?versions[versions.length-2].number:0;
 $("#vb-before").hidden=versions.length<2;$("#vb-after").hidden=versions.length<2;
 for(const [id,active] of [["vb-before",selectedVersion===before],["vb-after",latestVersion>0&&selectedVersion===latestVersion]]){
  $("#"+id).classList.toggle("on",active);$("#"+id).setAttribute("aria-pressed",String(active));
 }
 const label=selectedVersion?`引擎重算第 ${selectedVersion} 版`:"机器底稿";
 $("#version-description").textContent=label+(viewingHistory?" · 历史图面":" · 当前引擎图面");
 $("#version-label").textContent=label;
 $("#assumption-caption").textContent=`${label}的出图说明；未与集团真实图纸核对。`;
 renderAssumptions(doc,questionCatalog);
 $("#reference-amend-hint").hidden=!doc.reference_presentations;
 if(doc.reference_presentations){
  const receipt=referenceNode("article","","card t-ink");receipt.append(referenceNode("b","图前参考依据"));
  for(const item of doc.reference_presentations.mapping)receipt.append(referenceNode("p",`${item.hole_tag} → ${item.plane_tag} · ${item.relation}`));
  receipt.append(referenceNode("p","平面定义与交点引注见图面技术注记；仍须实际制图审核。","hint"));$("#answer-records").append(receipt);
 }
 $("#downloads").replaceChildren(...FORMATS.map(kind=>downloadLink(version.files[kind],`${jobId}-v${version.number}.${kind}`,kind.toUpperCase())));
 $("#quick-downloads").replaceChildren(referenceNode("span","下载当前预览：","hint"),...FORMATS.map(kind=>downloadLink(version.files[kind],`${jobId}-v${version.number}.${kind}`,kind.toUpperCase())));
 if(headVersion(currentJob)>latestVersion)$("#quick-downloads").append(downloadLink(`/jobs/${jobId}/effective`,`${jobId}-生效版本.dxf`,"下载当前生效 DXF"));
 if(headVersion(currentJob)>latestVersion){
  const detail=`生效版本第 ${headVersion(currentJob)} 版来自回传 DXF；当前预览仍为${label}。请下载生效版本核对；继续回答须重新提交 STEP，以免覆盖人工修改。`;
  $("#answer-receipt").textContent=detail;
  if(!isFrozen)showNotice("生效 DXF 与当前预览不同",detail,["latest"]);
 }
 refreshControls();
 $("#zoomctl").hidden=false;applyZoom();
}
async function viewVersion(number){
 if(pageBusy||answerBusy)return;
 const version=versions.find(item=>item.number===number);if(!version)return;
 const epoch=loadEpoch, preview=++previewEpoch;pageBusy=true;refreshControls();
 try{
  let data=versionCache.get(number);
  if(!data){const [svg,doc]=await Promise.all([api(version.files.svg,{},"text"),api(version.files.json)]);data={svg,doc}}
  if(epoch!==loadEpoch||preview!==previewEpoch)return;
  versionCache.set(number,data);
  paintVersion(version,data.svg,data.doc);setTaskURL(jobId,number);
 }catch(error){if(epoch===loadEpoch&&preview===previewEpoch){$("#version-select").value=String(selectedVersion);handleError(error)}}
 finally{if(epoch===loadEpoch){pageBusy=false;refreshControls()}}
}
function resetDrawing(id){
 if(jobId!==id){$("#side").scrollTop=0;$("#stage").scrollTop=0}
 draftPhase="loading";reviewDecision="";reviewPending=null;$("#review-confirm").hidden=true;$("#effective-ack").checked=false;$("#draft-banner").hidden=true;
 {
  isFrozen=false;$("#review-note").value="";$("#amended-file").value="";
  document.querySelectorAll('#reasons input:checked').forEach(i=>i.checked=false);
 }
 jobId=id;currentJob=null;drawingReady=false;questionsReady=false;questionList=[];choices.clear();
 referenceSnapshot=null;referenceChoices.clear();$("#reference-nav").hidden=true;$("#reference-nav").replaceChildren();$("#reference-questions").replaceChildren();$("#reference-questions").hidden=true;$("#reference-actions").hidden=true;
 $("#reference-amend-hint").hidden=true;
 versions=[];versionCache.clear();questionCatalog=[];latestVersion=0;selectedVersion=0;viewingHistory=false;draftSha="";
 $("#part").textContent=id||"尚未载入图纸";$("#drawing").textContent="正在读取图纸…";
 $("#annots").replaceChildren();$("#annots").hidden=true;
 $("#questions").replaceChildren();$("#questions-context").textContent="正在读取问题清单…";
 $("#questions-empty").hidden=true;$("#verbar").hidden=true;
 $("#zoomctl").hidden=true;$("#paper").removeAttribute("style");$("#drawing").removeAttribute("style");zoomMode="fit";
 $("#assumptions").replaceChildren();$("#answer-records").replaceChildren();$("#downloads").replaceChildren();$("#quick-downloads").replaceChildren();
 for(const name of ["diff","rvState","answers","answer-receipt"])$("#"+name).textContent="";
}
async function show(id,{historyMode="push",version=null}={}){
 if(!connected){goWorkspace();return}if(!saveDraft())return;
 const epoch=++loadEpoch;resetDrawing(id);document.body.dataset.screen="task";setTaskURL(id,version,historyMode);
 clearNotice();pageBusy=true;answerBusy=false;questionsReady=false;refreshControls();renderJobs();
 try{
  let job=await api(`/jobs/${id}?detail=1`);if(epoch!==loadEpoch)return;
  currentJob=job;setTaskTitle(job);
  if(["queued","running"].includes(job.state)){ $("#drawing").textContent="模型正在处理。可以返回工作台查看其他任务，处理会在后台继续。";job=await poll(id,epoch)}
  if(!job||epoch!==loadEpoch)return;currentJob=job;setTaskTitle(job);
  if(job.state==="waiting_reference"){
   const body=await api(`/jobs/${id}/reference-questions`);if(epoch!==loadEpoch)return;
   renderReferences(assertReferenceSnapshot(body));restoreDraft();return;
  }
  if(job.state!=="drawn"){draftPhase="readonly";renderResult(job);return}
  const list=engineVersions(job), latest=list[list.length-1];
  const [doc,svg,questions,original,base,reviews]=await Promise.all([
   api(latest.files.json),api(latest.files.svg,{},"text"),api(`/jobs/${id}/questions`),
   latest.number?api(list[0].files.json):Promise.resolve(null),
   api(headVersion(job)?`/jobs/${id}/effective`:list[0].files.dxf,{},"arrayBuffer"),api(`/jobs/${id}/review`)]);
  const hash=await sha256(base);if(epoch!==loadEpoch)return;
  if(job.context?.effective_sha&&hash!==job.context.effective_sha)throw Object.assign(new Error("图纸版本在读取中已变化，请刷新当前任务"),{status:409,body:{reason:"stale_base"}});
  currentJob=job;versions=list;latestVersion=latest.number;draftSha=hash;
  const blocking=new Set([...document.querySelectorAll('#reasons input[data-group="A"]')].map(input=>input.value));
  isFrozen=reviews.reviews.some(row=>(row.reasons||[]).some(reason=>blocking.has(reason)));
  questionCatalog=[...(original?.questions||doc.questions||[]),...questions.questions];
  versionCache=new Map([[latest.number,{svg,doc}]]);
  $("#version-select").replaceChildren(...versions.map(v=>{const option=document.createElement("option");option.value=String(v.number);option.textContent=v.number?`引擎重算第 ${v.number} 版`:"机器底稿";return option}));
  $("#verbar").hidden=false;renderQuestions(assertQuestions(questions));say("已出图 · 等待实际制图审核");paintVersion(latest,svg,doc);
  restoreDraft();if(isFrozen)handleError({status:409,body:{frozen:true}});
  if(version!==null&&Number(version)!==latest.number){pageBusy=false;await viewVersion(Number(version))}
 }catch(error){if(epoch===loadEpoch){drawingReady=false;questionsReady=false;$("#questions-context").textContent=error.message;
  $("#drawing").textContent=`未载入交付图：${error.message}`;handleError(error)}}
 finally{if(epoch===loadEpoch){pageBusy=false;refreshControls()}}
}
async function loadJobs(){
 if(!token||!connected)return;
 const session=sessionEpoch,request=++jobsEpoch;
 try{const j=await api("/jobs?detail=1");if(session!==sessionEpoch||request!==jobsEpoch)return;allJobs=j.jobs;renderJobs()}
 catch(error){if(session===sessionEpoch&&request===jobsEpoch){$("#job-list-status").textContent=error.status===401?"连接失效，请重新连接。":"列表读取失败，可重试。";if(error.status===401){connected=false;$("#connection-fields").hidden=false}handleError(error)}}
}
$("#jobs").addEventListener("click",event=>{const li=event.target.closest("#jobs li");if(li)show(li.dataset.job)});

$("#vb-before").onclick=()=>viewVersion(versions.length>1?versions[versions.length-2].number:0);
$("#vb-after").onclick=()=>viewVersion(latestVersion);
$("#back-to-current").onclick=()=>viewVersion(latestVersion);
$("#version-select").onchange=event=>{const version=versions.find(item=>String(item.number)===event.target.value);if(version)viewVersion(version.number)};
$("#refresh-job").onclick=()=>{if(jobId)show(jobId);loadJobs()};
$("#submit-answers").onclick=async()=>{
 if(!canAnswer()||$("#submit-answers").disabled)return;
 clearNotice();
 const id=jobId,epoch=++loadEpoch,answers=answerPayload();answerBusy=true;refreshControls();
 $("#answer-receipt").textContent="正在提交回答…";
 try{
  await api(`/jobs/${id}/answers`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reviewer:$("#who").value.trim(),answers})});
  const done=await poll(id,epoch);if(!done||epoch!==loadEpoch)return;
  if(done.state!=="drawn")throw Object.assign(new Error(done.error||(done.reasons||[]).join("；")||STATE[done.state]),{status:409,body:done});
  clearDraft();answerBusy=false;await show(id);
  if(jobId===id&&drawingReady){const diff=done.diff_from_previous||{};
   $("#answer-receipt").textContent=`回答已记录：${done.answered_count} 项，其中不知道 ${done.unknown_count} 项。相对上一版，${diff.changed_texts||0} 处文字变化，${diff.changed_positions||0} 处图元位置变化。`;
   $("#side").scrollTop=0;
  }
  await loadJobs();
 }catch(error){if(epoch===loadEpoch)handleError(error)}
 finally{if(epoch===loadEpoch){answerBusy=false;refreshControls()}}
};
$("#up").onsubmit=async event=>{
 event.preventDefault();if(!connected||uploadBusy)return;
 const file=$("#step-file").files[0];if(!file){$("#upload-status").textContent="请先选择 STEP / STP 文件。";$("#step-file").focus();return}
 if(!/\.(step|stp)$/i.test(file.name)){ $("#upload-status").textContent="请选择 .step 或 .stp 模型。";return }
 if(file.size>64*1024*1024){$("#upload-status").textContent="文件超过 64 MB，请选择较小的模型。";return}
 const session=sessionEpoch,navigation=loadEpoch;uploadBusy=true;refreshControls();$("#upload-status").textContent="正在上传模型…";
 try{const data=new FormData(event.currentTarget);
  const posted=await api("/jobs",{method:"POST",body:data});if(session!==sessionEpoch)return;
  $("#upload-status").textContent="模型已接收。";$("#step-file").value="";if(navigation===loadEpoch)await show(posted.job_id);await loadJobs();
 }catch(error){if(session===sessionEpoch){$("#upload-status").textContent=error.status===413?"文件超过 64 MB，请选择较小的模型。":error.status===401?"连接失效，请重新连接后再上传。":"未收到上传结果。请先刷新任务列表核对，再决定是否重试。";handleError(error)}}
 finally{if(session===sessionEpoch){uploadBusy=false;refreshControls()}}
};

async function ask(){
 if(!canInteract())return;
 const id=jobId,epoch=loadEpoch;
 try{const j=await api("/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({job_id:id,question:$("#q").value})});
  if(id!==jobId||epoch!==loadEpoch)return;
  $("#answers").replaceChildren(...j.answers.map(factCard));
 }catch(error){if(id===jobId&&epoch===loadEpoch)handleError(error,$("#answers"))}
}
$("#chips").replaceChildren(...CHIPS.map(text=>{const button=document.createElement("button");button.className="chip";button.type="button";button.textContent=text;return button}));
document.querySelectorAll("#chips .chip").forEach(button=>button.onclick=()=>{$("#q").value=button.textContent;ask()});
$("#qform").onsubmit=event=>{event.preventDefault();ask()};
function rv(decision){
 if(!canInteract()||$("#rvBtns [data-d="+decision+"]").disabled)return;
 reviewDecision=decision;$("#review-confirm").hidden=false;updateReviewControls();saveDraft();$("#review-confirm").scrollIntoView({block:"nearest"});
}
function reviewSummary(){
 const name={accepted:"直接用",amended:"改后采用",rejected:"驳回"}[reviewDecision];
 const reasons=[...document.querySelectorAll('#reasons input:checked')].map(input=>input.closest("label").textContent.trim());
 const current=headVersion(currentJob||{files:{}});
 $("#review-summary").textContent=`任务：${currentJob?.display_name||jobId}\n图纸：${current?`生效第 ${current} 版`:"机器底稿第 0 版"}${current>latestVersion?"（人工回传 DXF）":"（引擎图）"}\n操作人：${$("#who").value.trim()||"未填写"}\n结论：${name}\n原因：${reasons.join("；")||"无"}\n说明：${$("#review-note").value.trim()||"无"}`+
  (reviewDecision==="amended"&&current<=latestVersion?"\n尚未回传 CAD 修改文件。本次只记录改后采用结论；如需归档改后图，请先取消并回传 DXF。":"")+
  (reviewPending?"\n上次提交结果尚待核对。重试会沿用同一提交编号。":"");
}
async function submitReview(){
 if($("#confirm-review").disabled)return;
 const id=jobId,epoch=loadEpoch,hadPending=!!reviewPending;
 if(!reviewPending){reviewPending={decision:reviewDecision,reviewer:$("#who").value.trim(),
  reasons:[...document.querySelectorAll('#reasons input:checked')].map(i=>i.value),note:$("#review-note").value,
  reviewed_sha:draftSha,reviewed_version:headVersion(currentJob),submission_id:crypto.randomUUID()};}
 const body={...reviewPending},key=draftKey();
 if(!saveDraft()){if(!hadPending)reviewPending=null;$("#rvState").textContent="浏览器无法保存提交编号，本次未发送复核。请恢复本地存储后重试；原有待核对提交不会换号。";refreshControls();return}
 pageBusy=true;refreshControls();
 try{
  const history=await api(`/jobs/${id}/review`);let row=history.reviews.find(r=>r.submission_id===body.submission_id);
  if(id!==jobId||epoch!==loadEpoch)return;
  if(!row){const j=await api(`/jobs/${id}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});row=j.review}
  removePendingDraft(key,body.submission_id);
  if(id!==jobId||epoch!==loadEpoch)return;
  reviewPending=null;reviewDecision="";$("#review-confirm").hidden=true;$("#review-note").value="";
  document.querySelectorAll('#reasons input:checked').forEach(i=>i.checked=false);
  clearNotice();$("#rvState").textContent=`已记录：${{accepted:"直接用",amended:"改后采用",rejected:"驳回"}[row.decision]} · ${row.reviewer} · ${row.at}`;
  saveDraft();await loadJobs();if(id!==jobId||epoch!==loadEpoch)return;
  if(body.reasons.some(r=>document.querySelector(`#reasons input[value="${r}"]`)?.dataset.group==="A"))handleError({status:409,body:{frozen:true}},$("#rvState"));
 }catch(error){if(id===jobId&&epoch===loadEpoch){if(error.status&&error.status<500)reviewPending=null;saveDraft();handleError(error,$("#rvState"))}}
 finally{if(id===jobId&&epoch===loadEpoch){pageBusy=false;refreshControls()}}
}
document.querySelectorAll("#rvBtns .chip").forEach(button=>button.onclick=()=>rv(button.dataset.d));
$("#confirm-review").onclick=submitReview;
$("#cancel-review").onclick=()=>{if(reviewPending)return;reviewDecision="";$("#review-confirm").hidden=true;saveDraft();updateReviewControls()};

async function save(href,name){
 const session=sessionEpoch,epoch=loadEpoch;
 try{const blob=await api(href,{},"blob");if(session!==sessionEpoch)return;const url=URL.createObjectURL(blob),link=document.createElement("a");
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(error){if(session===sessionEpoch&&epoch===loadEpoch)handleError(error)}
}
document.addEventListener("click",event=>{const link=event.target.closest("a.dl");if(!link)return;
 event.preventDefault();save(link.href,link.getAttribute("download")||"drawform.dxf")});
$("#package-download").onclick=()=>{if(canInteract())save(`/jobs/${jobId}/package`,`${jobId}-交付包.zip`)};
$("#amend").onsubmit=async event=>{
 event.preventDefault();if(!canInteract()||$("#amend button").disabled)return;
 const file=document.querySelector("#amend input[type=file]").files[0];
 if(!file){$("#diff").textContent="先选改后的 ASCII DXF 文件";return}
 if(!draftSha){$("#diff").textContent="当前生效版本还未载入，请刷新任务后重试";return}
 const id=jobId,epoch=loadEpoch;
 const head=new Uint8Array(await file.slice(0,512).arrayBuffer());
 if(id!==jobId||epoch!==loadEpoch)return;
 if(head.includes(0)||new TextDecoder().decode(head).startsWith("AutoCAD Binary DXF")){$("#diff").textContent="请选择文本格式的 ASCII DXF，DWG 和二进制 DXF 无法回传";return}
 const data=new FormData();data.append("file",file);data.append("reviewer",$("#who").value);data.append("draft_sha",draftSha);
 pageBusy=true;refreshControls();
 try{const j=await api(`/jobs/${id}/amended`,{method:"POST",body:data});
  if(id!==jobId||epoch!==loadEpoch)return;
  draftSha=j.review.effective_sha;const version=j.review.version;$("#effective-ack").checked=false;
  const KIND={value_changed:"数值改了",symbol_changed:"符号改了",wrong_count:"数量改了",added:"新加一条",removed:"删掉一条"};
  $("#diff").textContent=`已生效（第 ${version} 版）。相对上一版共 ${(j.changes||[]).length} 处改动：`;
  if(j.blocking)isFrozen=true;
  $("#diff").append(downloadLink(`/jobs/${jobId}/effective`,`${jobId}-v${version}.dxf`,`下载第 ${version} 版接着改`));
  for(const change of j.changes||[]){const row=document.createElement("div");row.className="assume";
   row.textContent=`${KIND[change.action]||change.action}：${change.before||"—"} → ${change.after||"—"}；图上位置（${change.at.join(", ")}）`;$("#diff").append(row)}
  const updatedJob=await api(`/jobs/${id}?detail=1`);if(id!==jobId||epoch!==loadEpoch)return;currentJob=updatedJob;
  const preview=versionCache.get(selectedVersion),shownVersion=versions.find(item=>item.number===selectedVersion);
  if(preview&&shownVersion)paintVersion(shownVersion,preview.svg,preview.doc);
  if(isFrozen)handleError({status:409,body:{frozen:true}},$("#rvState"));
  await loadJobs();
 }catch(error){if(id===jobId&&epoch===loadEpoch)handleError(error,$("#diff"))}finally{if(id===jobId&&epoch===loadEpoch){pageBusy=false;refreshControls()}}
};
function setTaskTitle(job){
 $("#part").textContent=job.display_name||job.job_id;$("#task-title").textContent=`${job.display_name||`任务 ${job.job_id}`} · ${job.sheet||"A3"}`;
 $("#task-draft-status").textContent=`${STATE[job.state]||job.state} · ${job.job_id}`;
}
function setTaskURL(id,version=null,mode="replace"){
 const url=new URL(location.href);if(id)url.searchParams.set("job",id);else url.searchParams.delete("job");
 if(version!==null)url.searchParams.set("version",String(version));else url.searchParams.delete("version");
 if(mode!=="none"&&url.href!==location.href)history[mode==="push"?"pushState":"replaceState"]({},"",url);
}
function goWorkspace({historyMode="push"}={}){
 if(!saveDraft())return;loadEpoch++;previewEpoch++;pageBusy=false;answerBusy=false;
 document.body.dataset.screen="workspace";$("#part").textContent="任务工作台";$("#version-label").textContent="审图台";setTaskURL("",null,historyMode);say(connected?"已连接本机试点":"请连接服务");
 $("#connection-card").classList.toggle("connected",connected);$("#connection-fields").hidden=connected;$("#change-token").hidden=!connected;
 $("#upload-card").hidden=!connected;$("#tasks-card").hidden=!connected;
 $("#connection-state").textContent=connected?"已连接。令牌仅在当前标签页会话保存。":"粘贴本机试点的访问令牌后连接。令牌可从试点目录的令牌文件复制，或向管理员获取。";
 refreshControls();renderJobs();
}
async function connect(){
 const value=$("#token").value.trim();if(!value){$("#connection-state").textContent="请先粘贴访问令牌。";$("#token").focus();return}
 if(connectPromise&&pendingToken===value)return connectPromise;
 if(!saveDraft())return;
 if(connected&&value===token){goWorkspace();return loadJobs()}
 const epoch=++sessionEpoch;loadEpoch++;jobsEpoch++;connected=false;uploadBusy=false;
 token=value;pendingToken=value;sessionIdentity="";resetDrawing("");allJobs=[];renderJobs();
 $("#connection-state").textContent="正在验证连接…";$("#connect").disabled=true;
 connectPromise=(async()=>{
  try{const result=await api("/jobs?detail=1"),identity=await sha256(new TextEncoder().encode(value));
   if(epoch!==sessionEpoch)return;sessionIdentity=identity;connected=true;allJobs=result.jobs;
   try{sessionStorage.setItem("dfToken",value)}catch{storageFailed=true}
   $("#who").value=readSession(`dfWho:${identity}`)||"";
   const url=new URL(location.href),id=url.searchParams.get("job"),version=url.searchParams.get("version");
   goWorkspace({historyMode:"none"});if(id)await show(id,{historyMode:"none",version});
  }catch(error){if(epoch===sessionEpoch){connected=false;$("#connection-state").textContent=error.status===401?"令牌未通过验证。请检查是否完整复制，再点击连接。":`暂时无法连接：${error.message}`;$("#upload-card").hidden=true;$("#tasks-card").hidden=true;$("#connection-fields").hidden=false;$("#token").focus()}}
  finally{if(epoch===sessionEpoch){$("#connect").disabled=false;connectPromise=null;refreshControls()}}
 })();return connectPromise;
}
function renderJobs(){
 const query=$("#job-search").value.trim().toLowerCase(),filter=$("#job-filter").value;
 const rows=allJobs.slice().reverse().filter(job=>(!query||`${job.display_name||""} ${job.job_id}`.toLowerCase().includes(query))&&(!filter||(filter==="processing"?["queued","running"].includes(job.state):job.state===filter)));
 $("#job-list-status").textContent=`共 ${allJobs.length} 个任务，当前显示 ${rows.length} 个 · 新任务在前`+(rows.length?"":"。可调整搜索或筛选条件。");
 $("#jobs").replaceChildren(...rows.map(job=>{
  const li=$("#job-row").content.firstElementChild.cloneNode(true);li.dataset.job=job.job_id;li.dataset.state=job.state;
  const button=li.querySelector("button");button.setAttribute("aria-current",job.job_id===jobId?"page":"false");
  const date=job.created_at?new Date(job.created_at).toLocaleString("zh-CN"):"旧任务未记录上传时间";
  button.append(referenceNode("span",job.display_name||`未记录文件名 · ${job.job_id}`,"job-name"),referenceNode("span",`${date} · ${job.job_id}${job.name_origin==="verified_catalog"?" · 来源文件匹配":""}`,"job-meta"),referenceNode("span",STATE[job.state]||job.state,"job-state"));return li;
 }));
}
function draftKey(){return sessionIdentity&&jobId?`dfDraft:v2:${sessionIdentity}:${jobId}`:""}
function contextKey(){return currentJob?.context?.source_valid?JSON.stringify(currentJob.context):""}
function sameDraftContext(saved,current){
 if(saved===current)return true;
 try{
  const a=JSON.parse(saved),b=JSON.parse(current);
  if(!a||!b||Array.isArray(a)||Array.isArray(b)||typeof a!=="object"||typeof b!=="object")return false;
  // Existing tasks predating explicit paper selection were A3. Keep every
  // source/candidate/version check, comparing object fields rather than order.
  const normalize=value=>Object.entries({sheet:"A3",...value}).sort(([a],[b])=>a.localeCompare(b));
  return JSON.stringify(normalize(a))===JSON.stringify(normalize(b));
 }catch{return false}
}
function saveDraft(){
 if(draftMuted||draftPhase!=="ready"||!currentJob||!draftKey()||!contextKey())return true;
 const reasons=[...document.querySelectorAll('#reasons input:checked')].map(i=>i.value);
 const draft={context:contextKey(),choices:[...choices],references:[...referenceChoices],who:$("#who").value,
  reasons,note:$("#review-note").value,decision:reviewDecision,pending:reviewPending,activeReference};
 const dirty=localAnswers()||reasons.length||draft.note||reviewDecision||reviewPending;
 try{if(dirty)sessionStorage.setItem(draftKey(),JSON.stringify(draft));else sessionStorage.removeItem(draftKey());
  sessionStorage.setItem(`dfWho:${sessionIdentity}`,draft.who);storageFailed=false;
  $("#draft-banner").hidden=!dirty;$("#draft-message").textContent=dirty?"有未提交内容 · 已保存到当前标签页。切换任务后可回来继续。":"";return true;
 }catch{storageFailed=true;$("#draft-banner").hidden=false;$("#draft-message").textContent="浏览器无法保存草稿。请保留此页，或明确放弃未提交内容后再切换。";return !dirty}
}
function clearDraft(){
 draftPhase="ready";
 try{if(draftKey())sessionStorage.removeItem(draftKey())}catch{}
 choices.clear();referenceChoices.clear();reviewDecision="";reviewPending=null;$("#review-note").value="";
 document.querySelectorAll('#reasons input:checked').forEach(i=>i.checked=false);$("#draft-banner").hidden=true;
}
function restoreDraft(){
 draftPhase="ready";const raw=readSession(draftKey());if(!raw)return;let draft;try{draft=JSON.parse(raw);if(!draft||typeof draft!=="object"||!Array.isArray(draft.choices)||!Array.isArray(draft.references))throw new Error("bad draft")}catch{draftPhase="stale";$("#draft-banner").hidden=false;$("#draft-message").textContent="本地草稿无法读取。未套用任何选择，请明确放弃损坏草稿后继续。";return}
 if(!contextKey()||!sameDraftContext(draft.context,contextKey())){
  draftPhase="stale";$("#draft-banner").hidden=false;$("#draft-message").textContent="发现旧草稿，但模型、候选或图纸版本已变化。旧选择未套用，也不会被覆盖。请明确放弃旧稿后重新填写。";return;
 }
 draftMuted=true;
 try{
  $("#who").value=draft.who||$("#who").value;
  let incompatibleDraft=false;
  for(const [id,state] of draft.choices||[]){const q=questionList.find(q=>q.id===id);if(q&&(state.selected===null||state.selected===undefined||q.options.some(o=>o.id===state.selected))){
   const option=q.options.find(o=>o.id===state.selected),issue=option?optionIssue(q,option):"";
   choices.set(id,issue?{...state,confirmed:false}:state);if(issue)incompatibleDraft=true;
  }}
  for(const card of document.querySelectorAll("[data-question-card]")){const state=choices.get(card.dataset.question);if(state)card.querySelector("textarea").value=state.note||""}
  for(const [id,value] of draft.references||[]){const q=referenceSnapshot?.questions.find(q=>q.id===id);if(q&&(value===null||q.options.some(o=>o.id===value)))referenceChoices.set(id,value)}
  for(const input of document.querySelectorAll("#reference-questions input")){const qid=input.name.slice("reference-".length);input.checked=referenceChoices.has(qid)&&(referenceChoices.get(qid)===null?input.value==="unknown":referenceChoices.get(qid)===input.value)}
  for(const input of document.querySelectorAll("#reasons input"))input.checked=(draft.reasons||[]).includes(input.value);
  $("#review-note").value=draft.note||"";reviewDecision=draft.decision||"";reviewPending=draft.pending||null;$("#review-confirm").hidden=!reviewDecision;
  if(referenceSnapshot){focusReference(Math.min(draft.activeReference||0,referenceSnapshot.questions.length-1));syncReferenceNavigation()}
  $("#draft-banner").hidden=false;$("#draft-message").textContent=incompatibleDraft?"草稿含方向不匹配的基准，已取消该项确认；原选择、备注及其他有效项保留。请改选匹配方向的平面或选择我不知道。":"已恢复当前任务的未提交草稿。请继续核对后提交。";
 }finally{draftMuted=false}
}
function removePendingDraft(key,id){
 try{const raw=JSON.parse(sessionStorage.getItem(key)||"null");if(raw?.pending?.submission_id===id){raw.pending=null;raw.decision="";raw.reasons=[];raw.note="";sessionStorage.setItem(key,JSON.stringify(raw))}}catch{}
}
function renderResult(job){
 $("#question-count").textContent="无交付图";$("#questions-context").textContent="当前任务没有出图，暂无可复核的图纸。请查看原因和下一步。";
 const panel=referenceNode("div","","result-panel");panel.append(referenceNode("h2",job.state==="refused"?"检查结束，暂未生成图纸":"处理过程中出现异常"));
 const list=document.createElement("ul");for(const reason of job.reasons?.length?job.reasons:[job.error||"缺少可出图依据，请保留任务号联系开发者核查。"]){list.append(referenceNode("li",reason))}panel.append(list);
 for(const receipt of job.reference_receipt||[])panel.append(referenceNode("p",`此前已收到 ${receipt.count} 项参考选择（不知道 ${receipt.unknown_count} 项）· ${receipt.reviewer}。选择记录仍保留；未记为制图审核。`,"notice"));
 const next=(job.failed||[]).includes("sheet_overflow")?"当前纸幅不能满足可读性要求。参考确认已经记录；重复上传同一模型不会解决图面拥挤。可保留任务号，交由人工制图或后续布局开发处理。":(job.failed||[]).some(r=>r.includes("reference"))?"确认来源参考后，可准备新的 STEP 任务。也可先返回工作台继续其他零件。":"请依据上述原因检查模型范围或文件。修正模型后可新建任务，当前结果仍保留。";
 panel.append(referenceNode("p",next));const button=referenceNode("button","返回任务工作台","btn primary");button.type="button";button.onclick=()=>goWorkspace();panel.append(button);$("#drawing").replaceChildren(panel);
 say(STATE[job.state]||job.state);$("#version-label").textContent="无交付图";
}
$("#go-review").onclick=()=>{setOpen($("#review-section"),true);$("#review-section").scrollIntoView({block:"start"});$("#review-section .section-toggle").focus({preventScroll:true})};
$("#workspace-link").onclick=()=>goWorkspace();$("#other-tasks").onclick=()=>goWorkspace();
$("#connect").onclick=connect;$("#change-token").onclick=()=>{$("#connection-fields").hidden=false;$("#token").focus()};
$("#refresh-jobs").onclick=loadJobs;$("#job-search").oninput=renderJobs;$("#job-filter").onchange=renderJobs;
$("#reference-later").onclick=()=>goWorkspace();$("#next-reference").onclick=()=>{if(!referenceSnapshot)return;const index=referenceSnapshot.questions.findIndex(q=>!referenceChoices.has(q.id));if(index>=0)focusReference(index);else $("#submit-references").focus()};
$("#discard-draft").onclick=()=>{if(reviewPending){$("#draft-message").textContent="复核提交结果尚待核对，请先在复核区核对结果。";return}clearDraft();if(jobId)show(jobId,{historyMode:"none"})};
for(const event of ["input","change","click"])$("#side").addEventListener(event,e=>{if(e.target.closest("#questions,#reference-questions,#reasons,#review-note,#who")) {saveDraft();updateReviewControls()}});
$("#effective-ack").onchange=updateReviewControls;
window.addEventListener("beforeunload",event=>{if(!saveDraft()){event.preventDefault();event.returnValue=""}});
window.addEventListener("popstate",()=>{const url=new URL(location.href),id=url.searchParams.get("job");if(id&&connected)show(id,{historyMode:"none",version:url.searchParams.get("version")});else goWorkspace({historyMode:"none"})});
// Place authentication and task discovery at the start of the user journey; preserve the five review sections.
$("#connection-slot").append(document.querySelector('label[for="token"]'),$("#token"));$("#upload-slot").append($("#up"));$("#task-list-slot").append($("#jobs"));$("#legacy-jobs-title").hidden=true;
document.body.dataset.screen="workspace";refreshControls();renderJobs();if(token)connect();

</script>
</body>
</html>
"""

# 理由分类只在 `drawform.review.REASONS` 里有一份。在页面里另抄一遍，迟早会出现「页面上能选、后端说不是合法分类」。
PAGE = PAGE.replace("__REASONS__", "".join(
    f'<label class="{"reason-blocking" if REASON_GROUPS[key] == "A" else ""}">'
    f'<input type="checkbox" name="reason" value="{key}" data-group="{REASON_GROUPS[key]}" '
    f'data-note-required="{str(key in NOTE_REQUIRED_REASONS).lower()}">'
    f'<span>{"⚠ A 阻断 · " if REASON_GROUPS[key] == "A" else ""}{text}</span></label>'
    for key, text in REASONS.items()))
PAGE = PAGE.replace("__NOTE_LIMIT__", str(MAX_NOTE_LENGTH))


def create_app(work_dir, token: str | None = None,
               max_upload_bytes: int = MAX_UPLOAD_BYTES, *, auto_resume: bool | None = None) -> Flask:
    """建一个服务实例，并起那条唯一的 worker 线程（随进程存活）。

    `token` 为空时读 `DRAWFORM_TOKEN`；两处都没给就**拒绝启动**。
    """
    from .production import INCOMPLETE_RESTORE
    marker = pathlib.Path(work_dir) / INCOMPLETE_RESTORE
    if marker.exists() or marker.is_symlink():
        raise RuntimeError('工作目录恢复未完成，不能启动服务。')
    token = os.environ.get(TOKEN_ENV, "") if token is None else token
    tokens = _parse_tokens(token)
    if not tokens:
        raise RuntimeError(
            f"没有配置访问令牌：设 {TOKEN_ENV} 环境变量或传 token=。"
            "拒绝以「谁连上都能用」的状态启动。")

    app = Flask(__name__)
    from .frontend import configure_frontend, home_response, PUBLIC_ENDPOINTS
    configure_frontend(app)
    app.config["MAX_CONTENT_LENGTH"] = max_upload_bytes
    app.config['DRAWFORM_AUTO_RESUME'] = os.environ.get('DRAWFORM_AUTO_RESUME') == '1' if auto_resume is None else bool(auto_resume)
    work_dir = pathlib.Path(work_dir).resolve()
    # 审计与整条版本链保留；到期仅由留存工具列出待人工确认清单。
    reviews = ReviewLog(pathlib.Path(work_dir) / "reviews.jsonl")
    source_names = read_source_names(os.environ.get("DRAWFORM_SOURCE_NAMES", ""))
    queue = JobQueue(work_dir, review_log=reviews, auto_resume=app.config['DRAWFORM_AUTO_RESUME'])
    queue.start()
    from drawform.workbench.api import register as register_workbench
    register_workbench(app, work_dir, queue)

    @app.before_request
    def require_token():
        # 公开空壳页面及固定构建资源；工程数据的边界仍划在 API 上。
        if request.path in ("/", "/workbench/") or request.endpoint in PUBLIC_ENDPOINTS:
            return
        given = request.headers.get("Authorization", "")
        raw = given.removeprefix("Bearer ").strip()
        # 定长比较：令牌是共享密钥，逐字节短路比较会把它变成可测的侧信道。
        matched = next((t for t in tokens if hmac.compare_digest(raw, t)), None)
        if matched is None:
            abort(401)
        g.token_user = tokens[matched]
        # 审计里记指纹不记明文：令牌本身是可复制的密钥，不该进日志。
        g.token_scope = hashlib.sha256(matched.encode()).hexdigest()
        g.token_hint = g.token_scope[:8]

    def job_or_404(job_id: str):
        try:
            return queue.get(job_id)
        except KeyError:
            abort(404)

    @app.get("/")
    def index():
        return home_response(app, PAGE)

    @app.get("/jobs")
    def jobs():
        detailed = request.args.get("detail") == "1"
        return jsonify(jobs=[{"job_id": job.id, "state": job.state,
                              "failed": list(job.failed),
                              **(dict(sheet=job.sheet, **metadata(job, source_names)) if detailed else {})}
                             for job in queue.listing()])

    @app.post("/jobs")
    def submit():
        sheets = request.form.getlist('sheet')
        if len(sheets) > 1 or (sheets and sheets[0] not in ('A3', 'A2')):
            return jsonify(error="纸幅请选择 A3 或 A2；每次上传只能选择一个幅面"), 400
        sheet = sheets[0] if sheets else 'A3'
        upload = request.files.get("step")
        if upload is None or not upload.filename:
            return jsonify(error="请求里要有 step 文件"), 400
        with tempfile.TemporaryDirectory() as scratch:
            stored = pathlib.Path(scratch) / _STORED_NAME
            upload.save(stored)
            request_key = request.headers.get('Idempotency-Key','').strip()
            if request_key:
                try:
                    job_id = queue.submit_once(stored,request_id='http:'+getattr(g,'token_scope','')+':'+request_key,
                            expected_sha=hashlib.sha256(stored.read_bytes()).hexdigest(),
                            display_name=display_name(upload.filename),sheet=sheet)
                except ValueError as error:return jsonify(error=str(error)),409
            else: job_id = queue.submit(stored, display_name=display_name(upload.filename), sheet=sheet)
        return jsonify(job_id=job_id, state="queued"), 202

    @app.get("/jobs/<job_id>")
    def status(job_id: str):
        job = job_or_404(job_id)
        files = {kind: f"/jobs/{job.id}/files/{kind}" for kind, _path in job.files}
        kinds = {path: kind for kind, path in job.files}
        files.update({kind: f"/jobs/{job.id}/files/{kinds[path]}"
                      for kind, path in current_files(job).items()})
        body = dict(job_id=job.id, state=job.state, files=files,
                    failed=list(job.failed), reasons=list(job.reasons), error=job.error)
        if job.answer_runs:
            answers = answers_of(job)
            latest = latest_answers(job)
            body.update(questions=[asdict(q) for q in job.questions],
                        answered_count=len(answers),
                        unknown_count=sum(a.chosen_option_id is None for a in answers),
                        version=len(versions_of(job)),
                        diff_from_previous=dict(latest.diff) if latest is not None else {})
            if job.answer_runs[-1].reason:
                body["reason"] = job.answer_runs[-1].reason
        if request.args.get("detail") == "1":
            body.update(sheet=job.sheet, **metadata(job, source_names), context=task_context(job),
                        reference_receipt=reference_receipt(job))
        return jsonify(body)

    @app.get("/jobs/<job_id>/reference-questions")
    def reference_questions(job_id: str):
        job=job_or_404(job_id)
        if (job.state!=WAITING_REFERENCE or not isinstance(job.reference_snapshot,dict)
                or job.reference_snapshot.get('version')!=BASIS_VERSION):
            return jsonify(error='当前没有可确认的图前参考，请查看状态或重新提交STEP',
                           state=job.state,failed=list(job.failed),reasons=list(job.reasons)),409
        try:source_sha=hashlib.sha256(job.source.read_bytes()).hexdigest()
        except OSError:source_sha=None
        if source_sha!=job.reference_snapshot.get('source_sha256'):
            return jsonify(error='STEP来源已变化或不可读，请重新提交；旧候选已失效',
                           reason='stale_reference',state=job.state),409
        return jsonify(job_id=job.id,state=job.state,snapshot=job.reference_snapshot)

    @app.post("/jobs/<job_id>/reference-answers")
    def reference_answers(job_id: str):
        job_or_404(job_id)
        body=request.get_json(silent=True)
        if (not isinstance(body,dict) or set(body)!={'reviewer','basis_sha','answers'}
                or not isinstance(body.get('reviewer'),str)
                or not isinstance(body.get('answers'),list)):
            return jsonify(error='仅接受reviewer、basis_sha和answers；不能手输坐标、数值或向量'),400
        try:
            run=queue.submit_reference_answers(job_id,
                {'basis_sha':body['basis_sha'],'answers':body['answers']},body['reviewer'],
                getattr(g,'token_hint',''),getattr(g,'token_user',''))
        except ReferenceInputError as error:
            job=queue.get(job_id)
            return jsonify(error=str(error),reason=error.reason,state=job.state,
                           failed=list(job.failed),reasons=list(job.reasons)),error.status_code
        return jsonify(job_id=job_id,state=run['state'],reference_run=run['key']),202

    @app.get("/jobs/<job_id>/questions")
    def job_questions(job_id: str):
        """只搬运该次出图落盘的问题清单；没有依据不能冒充没有问题。"""
        job = job_or_404(job_id)
        if job.state != DRAWN:
            return jsonify(error="这张图没有出图成功，没有可确认的对象。"
                                 "请查看任务状态，等待出图或重新提交 STEP",
                           state=job.state, failed=list(job.failed),
                           reasons=list(job.reasons)), 409
        source = current_files(job).get("json")
        document = None
        if source is not None:
            try:
                document = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        if (not isinstance(document, dict)
                or not isinstance(document.get("questions"), list)
                or not isinstance(document.get("truncated"), bool)):
            return jsonify(error="这张图没有可读取的问题清单依据，请重新出图后再确认",
                           state=job.state, failed=list(job.failed),
                           reasons=list(job.reasons)), 409
        return jsonify(job_id=job.id, questions=document["questions"],
                       truncated=document["truncated"])

    @app.post("/jobs/<job_id>/answers")
    def job_answers(job_id: str):
        job_or_404(job_id)
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) - {"reviewer", "answers"}
                or not isinstance(body.get("reviewer"), str) or not body["reviewer"].strip()
                or not isinstance(body.get("answers"), list)):
            return jsonify(error="请求须包含非空 reviewer 和 answers 数组"), 400
        answers = []
        try:
            for item in body["answers"]:
                if (not isinstance(item, dict) or set(item) - {"question_id", "option_id", "free_note"}
                        or not {"question_id", "option_id"} <= set(item)):
                    raise AnswerError("每条答案只接受 question_id、option_id、free_note，前两项必填")
                answers.append(Answer(item["question_id"], item["option_id"], item.get("free_note", "")))
            run = queue.submit_answers(job_id, tuple(answers), body["reviewer"].strip(),
                                       getattr(g, "token_hint", ""), getattr(g, "token_user", ""))
        except AnswerError as error:
            job = queue.get(job_id)
            return jsonify(error=str(error), reason=error.reason, state=job.state,
                           failed=list(job.failed), reasons=list(job.reasons)), error.status_code
        except ValueError as error:
            return jsonify(error=str(error), reason="invalid_answer"), 400
        except StaleBase as error:
            job = queue.get(job_id)
            return jsonify(error=str(error), reason="stale_base", state=job.state,
                           failed=list(job.failed), reasons=list(job.reasons),
                           head=len(versions_of(job))), 409
        return jsonify(job_id=job_id, state=run.state, answered=len(run.answers), version=run.version), 202

    @app.post("/ask")
    def ask_about_drawing():
        """只回答这张图算得出的事实；词表之外的问题一律拒答，不猜。"""
        body = request.get_json(silent=True) or {}
        question = str(body.get("question", "")).strip()
        job = job_or_404(str(body.get("job_id", "")))
        if not question:
            return jsonify(error="question 不能为空"), 400
        if job.state != DRAWN:
            return jsonify(error="这张图没有出图成功，请等待出图或重新提交后再问",
                           state=job.state, failed=list(job.failed)), 409
        source = current_files(job).get("json")
        if source is None:
            # 被门拦下的任务没有依据可问。回 409 而不是给一个空答案，
            # 否则前端会把"没画出来"读成"这张图没有问题"。
            return jsonify(error="这张图没有出图，没有可问答的依据",
                           failed=list(job.failed)), 409
        document = json.loads(pathlib.Path(source).read_text())
        answers = knowledge.ask(question, knowledge.index_of(
            document.get("assumptions", [])))
        return jsonify(question=question,
                       answers=[asdict(fact) for fact in answers])

    @app.post("/jobs/<job_id>/review")
    def review_submit(job_id: str):
        """记一条复核结论。被门拦下或崩掉的任务**不给记**（409）：
        那会在统计里变成"这张图被接受了"的假账。"""
        job_or_404(job_id)
        try:
            row = submit_review(queue, reviews, job_id, request.get_json(silent=True),
                                getattr(g, "token_hint", ""), getattr(g, "token_user", ""), getattr(g, "token_scope", ""))
        except ReviewConflict as conflict:
            return jsonify(error=str(conflict), **conflict.context), 409
        except ReviewError as bad:
            return jsonify(error=str(bad), reason_options=sorted(REASONS)), 400
        return jsonify(review=row), 201

    @app.get("/jobs/<job_id>/review")
    def review_history(job_id: str):
        job = job_or_404(job_id)
        return jsonify(reviews=reviews.for_job(job.id),
                       damaged_lines=reviews.damaged_lines)

    @app.get("/review/stats")
    def review_stats():
        """试点期那五个数里"有没有被采用 / 为什么驳回"的自动来源。"""
        return jsonify(reviews.stats())

    def _draft_of(job: Job):
        return dict(job.files).get("dxf")

    @app.post("/jobs/<job_id>/amended")
    def upload_effective(job_id: str):
        """收下工程师改后的图纸，登记为**下一个**生效版本。底稿一个字节都不动。

        两道校验各自挡掉一种事故：基线哈希对不上说明他改的是旧版或别人的图；
        解析不了说明传的是 DWG / 二进制 DXF —— 这台机器读不了，收下就是空头承诺。
        """
        job = job_or_404(job_id)
        if job.state != DRAWN:
            return jsonify(error="这张图没有出图成功，没有可回写的底稿",
                           state=job.state, failed=list(job.failed)), 409
        upload = request.files.get("file")
        reviewer = (request.form.get("reviewer") or "").strip()
        draft_sha = (request.form.get("draft_sha") or "").strip()
        if upload is None or not reviewer:
            return jsonify(error="要有改后图文件与非空的复核人"), 400

        data = upload.read()
        with tempfile.TemporaryDirectory() as scratch:
            probe = pathlib.Path(scratch) / "probe.dxf"
            probe.write_bytes(data)
            if not _is_readable_dxf(probe):
                return jsonify(error="改后图必须是能解析的 ASCII DXF（DWG 与二进制 DXF 读不了）"), 400
        with queue.review_guard():
            try:
                # 基线校验在队列的锁里做，见 `JobQueue.put_version`。
                version, row, summary = queue.publish_amended(job.id,data,draft_sha,
                        reviewer=reviewer,note=(request.form.get('note') or '').strip(),
                        token_hint=getattr(g,'token_hint',''),token_user=getattr(g,'token_user',''))
            except StaleBase as stale:
                return jsonify(error=str(stale), head=len(versions_of(job))), 409
            except ReferenceInputError as error:
                return jsonify(error=str(error),reason=error.reason,head=len(versions_of(job))),error.status_code

            # 差异只与**直接父版**比：拿第三版去比机器底稿会把中间几版折叠成一个数，
            # 而那正是"谁改的、什么时候改的"这条链最有用的部分。
            actions, changes = summary['actions'], summary['changes']
            return jsonify(review=row, diff=actions, changes=changes,
                           blocking=summary['blocking']), 201

    @app.get("/jobs/<job_id>/effective")
    def effective_version(job_id: str):
        """当前生效版本。PDM 归档与"在这版上接着改"都认这一个 URL，不必知道第几版。"""
        job = job_or_404(job_id)
        head = head_of(job)
        if head is None:
            return jsonify(error="还没有生效版本，机器底稿在 /files/dxf", head=0), 404
        return send_file(head, as_attachment=True,
                         download_name=f"{job.id}-生效版本.dxf")

    @app.get("/jobs/<job_id>/diff")
    def diff_of(job_id: str):
        """头版 vs 机器底稿：答的是"机器一共要改几处"，不是最后一步改了什么。"""
        job = job_or_404(job_id)
        draft, head = _draft_of(job), head_of(job)
        if draft is None or head is None:
            return jsonify(error="还没有生效版本，无差异可比"), 404
        diff = compare(draft, head)
        return jsonify(diff=diff.as_dict(), changes=[dict(i) for i in diff.items],
                       version=len(versions_of(job)))

    @app.get("/jobs/<job_id>/package")
    def delivery_package(job_id: str):
        """交付包：整条生效链 + 机器底稿 + JSON + 审计行。PDM 入库要的就是这一包。"""
        job = job_or_404(job_id)
        try: captured=queue.package_snapshot(job.id)
        except StaleBase as error:return jsonify(error=str(error)),409
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name,data in captured.items():archive.writestr(name,data)
        buffer.seek(0)
        return send_file(buffer, mimetype="application/zip", as_attachment=True,
                         download_name=f"{job.id}-交付包.zip")

    @app.get("/jobs/<job_id>/files/<kind>")
    def download(job_id: str, kind: str):
        job = job_or_404(job_id)
        # 只认任务记录里已经产出的那几个：kind 不参与拼路径，越不了界。
        path = dict(job.files).get(kind)
        if path is None:
            abort(404)
        name = (f"{job_id}.{kind}" if kind in ("dxf", "svg", "pdf", "json", "dwg")
                else f"{job_id}-{kind}{path.suffix}")
        return send_file(path, as_attachment=True, download_name=name)

    return app


def _sha_of(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _parse_tokens(raw: str) -> dict[str, str]:
    """`tokA,tokB` 或 `设计-甲=tokA,工艺-乙=tokB` → {令牌: 名字或空串}。"""
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, value = item.partition("=")
        out[(value.strip() if sep else name).strip()] = name.strip() if sep else ""
    return out


def _is_readable_dxf(path) -> bool:
    import ezdxf

    try:
        ezdxf.readfile(str(path))
    except Exception:
        return False
    return True
