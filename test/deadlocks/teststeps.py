import commands

testconf_contents = []

def iModel(description):
    """
    create model.lsts according to the description
    """
    global testconf_contents
    if description == "dead":
        model = ''
    elif description == "dead-after-input":
        model = 'T(istate, "iNop", dead)'
    elif description == "outonly":
        model = 'T(istate, "oNop", dead)'
    elif description == "outonly-after-input":
        model = 'T(istate, "iNop", ostate)' + 'T(ostate, "oNop", dead)'
    else:
        raise Exception(f"teststeps.py: don't know how to make model '{description}'")
    modelcmd = f"fmbt-gt -o model.lsts 'P(istate,p) -> {model}'"
    s,o = commands.getstatusoutput(modelcmd)
    if s != 0:
        raise Exception("teststeps.py: fmbt-gt error, try: \"%s\"" % (modelcmd,))
    testconf_contents = ['model = "model.lsts"']

def iActions(i=0, o=0):
    """
    adds extra actions to model.lsts
    """
    model = ''.join(f'T(unreachable, "iExtra{n}", istate)' for n in xrange(i))
    for n in xrange(o):
        model += f'T(unreachable, "oExtra{n}", istate)'
    modelcmd = "fmbt-gt -i model.lsts -o model.lsts --keep-labels 'P(istate, \"gt:istate\") -> %s'" % (model,)
    s,o = commands.getstatusoutput(modelcmd)
    if s != 0:
        raise Exception("teststeps.py: fmbt-gt error, try: \"%s\"" % (modelcmd,))

def iHeur(heuristics):
    """
    define heuristics to be used in the test configuration
    """
    global testconf_contents
    testconf_contents.append(f'heuristics = "{heuristics}"')

def iEnd(end_condition):
    if end_condition is None: return
    else:
        testconf_contents.append(f'inconc = "{end_condition}"')

def iRun(expected_verdict):
    testconf_contents.append('on_fail = "exit:10"')
    testconf_contents.append('on_inconc = "exit:11"')
    file("test.conf", "w").write('\n'.join(testconf_contents))
    # Todo: check that we'll get the expected verdict
