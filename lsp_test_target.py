def compute_total(a, b):
    result = a + b
    return resul  # typo: 未定义名 resul + 未使用 result


def long_function_name_that_has_a_very_long_signature_and_is_hard_to_read(x, y, z, q, w, e):
    """这一行很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长"""
    return undefined_name + x


# 行长诊断: 下一行超过 100 字符，应触发 mock-lsp 行长诊断(severity 3=info)
this_is_a_very_long_variable_assignment_line_that_exceeds_one_hundred_characters_for_length_checking_purpose = 12345
