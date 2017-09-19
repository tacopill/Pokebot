def db(cmd):
    cmd._db = True
    return cmd


def no_delete(cmd):
    cmd._delete_ctx = False
    return cmd
