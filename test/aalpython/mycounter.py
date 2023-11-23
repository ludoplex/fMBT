log_filename="/tmp/fmbt.test.aal-python.mycounter.log"
file(log_filename, "w").close()
def log(msg):
    file(log_filename, "a").write(msg + "\n")

def foo():
    pass

def direction_changed(i):
    log(f'change direction on value {i}')
    log(f'    dec called: {dec_called}')

