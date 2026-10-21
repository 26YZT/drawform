"""Real process death test with a TERM-ignoring synthetic child; no CAD."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from drawform.workbench import child_guard

def test_dead_controller_cannot_leave_a_term_ignoring_writer(tmp_path):
    child=tmp_path/'writer.py';tick=tmp_path/'tick'
    child.write_text('import signal,time,sys\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\np=Path(sys.argv[1])\nwhile True:\n p.write_text(str(time.time()))\n time.sleep(.05)\n')
    parent=tmp_path/'parent.py';meta=tmp_path/'guard'
    parent.write_text('import subprocess,sys,os,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid()),sys.executable,sys.argv[2],sys.argv[3]],start_new_session=True)\nPath(sys.argv[4]).write_text(str(p.pid))\nwhile True:time.sleep(.1)\n')
    process=subprocess.Popen([sys.executable,str(parent),str(Path(child_guard.__file__)),str(child),str(tick),str(meta)])
    try:
        deadline=time.monotonic()+10
        while not tick.exists() and time.monotonic()<deadline:time.sleep(.05)
        assert tick.exists()
        process.kill();process.wait(timeout=5)
        time.sleep(3.8)
        before=tick.read_bytes();time.sleep(.25)
        assert tick.read_bytes()==before
    finally:
        if process.poll() is None:process.kill();process.wait()
        if meta.exists():
            try:os.killpg(int(meta.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
