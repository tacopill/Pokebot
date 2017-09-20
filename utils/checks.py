def no_delete(cmd):
    cmd._delete_ctx = False
    return cmd
