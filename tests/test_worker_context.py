
import pytest

from certus_vss.inference.watchdog import (
    validate_acquisition_context,
)


class FakeTokenizer:
    def __init__(self, token_count):
        self.token_count = token_count
        self.received_prompt = None
        self.add_bos = None

    def tokenize(self, prompt, add_bos=True):
        self.received_prompt = prompt
        self.add_bos = add_bos
        return list(range(self.token_count))


def test_acquisition_context_accepts_prompt_within_budget():
    llm = FakeTokenizer(token_count=100)

    result = validate_acquisition_context(
        llm, "small evidence prompt", max_tokens=128
    )

    assert result == 100
    assert llm.received_prompt == b"small evidence prompt"
    assert llm.add_bos is True


def test_acquisition_context_rejects_oversized_prompt():
    llm = FakeTokenizer(token_count=129)

    with pytest.raises(ValueError, match="exceeds context budget"):
        validate_acquisition_context(
            llm, "large evidence prompt", max_tokens=128
        )


def test_acquisition_context_accepts_exact_boundary():
    llm = FakeTokenizer(token_count=128)

    result = validate_acquisition_context(
        llm, "boundary prompt", max_tokens=128
    )

    assert result == 128


def test_acquisition_context_rejects_zero_output_tokens():
    llm = FakeTokenizer(token_count=10)

    with pytest.raises(ValueError, match="max_tokens must be positive"):
        validate_acquisition_context(
            llm, "prompt", max_tokens=0
        )
