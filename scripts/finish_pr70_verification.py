from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/integration/test_issue47_final_journey.py",
    '        "occurred_at": "2026-08-24T00:01:00+00:00",\n'
    '        "source": "razorpay_test",\n'
    "    }",
    '        "occurred_at": "2026-08-24T00:01:00+00:00",\n'
    '        "source": "razorpay_test",\n'
    '        "authenticity_verified": True,\n'
    "    }",
)

replace_once(
    "tests/integration/test_recovery_actions.py",
    '        "occurred_at": "2024-08-24T06:31:40+00:00",\n'
    '        "source": "razorpay_test",\n'
    "    }",
    '        "occurred_at": "2024-08-24T06:31:40+00:00",\n'
    '        "source": "razorpay_test",\n'
    '        "authenticity_verified": True,\n'
    "    }",
)
