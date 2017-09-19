def wrap(to_wrap, wrap_with, sep=' '):
    return f"{wrap_with}{sep}{to_wrap}{sep}{wrap_with}"


def unique(it, key):
    new = []
    added = []
    for i in it:
        k = key(i)
        if k not in added:
            new.append(i)
            added.append(k)
    return new
