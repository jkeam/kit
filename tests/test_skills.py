"""Tests for skill execution sandboxing and success-rate tracking in
runtime/skills.py.
"""

import pytest

from runtime.skills import SkillsManager


@pytest.fixture
def skills_manager(tmp_path):
    return SkillsManager(workspace_dir=str(tmp_path))


def test_skill_cannot_open_files(skills_manager):
    skills_manager.create_skill(
        name="try-open",
        description="attempts filesystem access",
        code="def main():\n    return open('/etc/passwd').read()",
    )
    result = skills_manager.execute_skill("try-open")
    assert "Error executing skill" in result


def test_skill_cannot_import_os(skills_manager):
    skills_manager.create_skill(
        name="try-import-os",
        description="attempts to import os",
        code="import os\ndef main():\n    return os.getcwd()",
    )
    result = skills_manager.execute_skill("try-import-os")
    assert "Error executing skill" in result


def test_skill_cannot_use_eval_or_exec(skills_manager):
    skills_manager.create_skill(
        name="try-eval",
        description="attempts eval",
        code="def main():\n    return eval('1 + 1')",
    )
    result = skills_manager.execute_skill("try-eval")
    assert "Error executing skill" in result


def test_skill_can_use_safe_modules(skills_manager):
    skills_manager.create_skill(
        name="safe-math",
        description="uses math and json, both allowlisted",
        code="import math, json\ndef main():\n    return json.dumps({'pi': round(math.pi, 2)})",
    )
    result = skills_manager.execute_skill("safe-math")
    assert "3.14" in result


def test_success_rate_all_successes(skills_manager):
    skills_manager.create_skill(name="always-ok", description="d", code="def main():\n    return 'ok'")
    skills_manager.execute_skill("always-ok")
    skills_manager.execute_skill("always-ok")
    info = skills_manager.get_skill_info("always-ok")
    assert info["usage_count"] == 2
    assert info["success_count"] == 2
    assert info["success_rate"] == 1.0


def test_success_rate_all_failures(skills_manager):
    skills_manager.create_skill(name="always-fail", description="d", code="def main():\n    raise ValueError('boom')")
    skills_manager.execute_skill("always-fail")
    skills_manager.execute_skill("always-fail")
    info = skills_manager.get_skill_info("always-fail")
    assert info["usage_count"] == 2
    assert info["success_count"] == 0
    assert info["success_rate"] == 0.0


def test_success_rate_reflects_actual_mixed_outcomes(skills_manager):
    """Regression test for the old bug where a failure-then-success sequence
    did not land on the true 50% success rate."""
    skills_manager.create_skill(name="mixed", description="d", code="def main():\n    raise ValueError('boom')")
    skills_manager.execute_skill("mixed")  # fails: usage=1, success=0

    skill_file = skills_manager.skills_dir / "mixed.py"
    skill_file.write_text("def main():\n    return 'ok'")
    skills_manager.execute_skill("mixed")  # succeeds: usage=2, success=1

    info = skills_manager.get_skill_info("mixed")
    assert info["usage_count"] == 2
    assert info["success_count"] == 1
    assert info["success_rate"] == 0.5
