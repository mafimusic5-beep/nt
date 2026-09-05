from src.backend.utils.security import ACTIVATION_CODE_LENGTH, generate_activation_code


def test_generate_activation_code_format_and_entropy() -> None:
    codes = {generate_activation_code() for _ in range(500)}
    assert len(codes) == 500
    for code in codes:
        assert len(code) == ACTIVATION_CODE_LENGTH == 11
        assert code.isalnum()
        assert code.upper() == code
