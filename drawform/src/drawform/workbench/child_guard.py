"""Terminate an isolated CAD child if its controlling service disappears."""
import os
import signal
import subprocess
import sys
import time


def main():
    parent=int(sys.argv[1])
    if os.getppid()!=parent:return 125
    stopping=[False]
    def terminate(_signal,_frame):stopping[0]=True
    signal.signal(signal.SIGTERM,terminate)
    child=subprocess.Popen(sys.argv[2:])
    deadline=None
    try:
        while child.poll() is None:
            if (os.getppid()!=parent or stopping[0]) and deadline is None:
                deadline=time.monotonic()+3
                os.killpg(os.getpgrp(),signal.SIGTERM)
            if deadline is not None and time.monotonic()>deadline:
                os.killpg(os.getpgrp(),signal.SIGKILL)
            time.sleep(.1)
        return 125 if deadline is not None else child.returncode
    finally:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(os.getpgrp(),signal.SIGKILL)


if __name__=='__main__':raise SystemExit(main())
