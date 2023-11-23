# ps.aal adapter code

import os
import subprocess
import _thread

import fmbt

last_bg_pid = 0

def readlines_to_adapterlog(file_obj, prefix):
    while 1:
        if line := file_obj.readline():
            fmbt.adapterlog(f"{prefix}{line}")
        else:
            break

def soe(cmd, stdin="", cwd=None, env=None):
    """Run cmd, return (status, stdout, stderr)"""
    run_env = dict(os.environ)
    if env is not None:
        run_env.update(env)
    fmbt.adapterlog("%s: soe run %r" % (fmbt.actionName(), cmd))
    try:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # close_fds=True,
            cwd=cwd,
            env=run_env)
        out, err = p.communicate(input=stdin)
    except Exception as e:
        return (None, None, str(e))
    fmbt.fmbtlog("%s: soe got status=%r out=%r err=%r" % (fmbt.actionName(), p.returncode, out, err))
    return (p.returncode, out, err)

def bg(cmd):
    global last_bg_pid
    fmbt.fmbtlog("%s: bg run %r" % (fmbt.actionName(), cmd))
    p = subprocess.Popen(cmd, shell=False,
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    fmbt.fmbtlog(f"{fmbt.actionName()}: bg pid {p.pid}")
    _thread.start_new_thread(readlines_to_adapterlog, (p.stdout, f"{p.pid} out: "))
    _thread.start_new_thread(readlines_to_adapterlog, (p.stderr, f"{p.pid} err: "))
    last_bg_pid = p.pid
