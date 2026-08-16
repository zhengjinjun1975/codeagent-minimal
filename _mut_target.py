def classify(n):
    if n > 0:
        return "pos"
    elif n < 0:
        return "neg"
    return "zero"

def add(a, b):
    return a + b

def is_ok(flag):
    return flag and True

def scale(x, factor=2):
    return x * factor
