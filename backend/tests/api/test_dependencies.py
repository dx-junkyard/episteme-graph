"""Auth ヘルパー（JWT 生成・パスワードハッシュ）の単体テスト。"""

from __future__ import annotations

import jwt
import pytest

from api.dependencies import (
    ROLE_STUDENT,
    ROLE_SYSTEM_ADMIN,
    ROLE_TEACHER,
    _app_role_to_pg_role,
    _create_token,
    _hash_password,
    _pg_role_to_app_role,
    _verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_and_verify(self):
        plain = "my-secure-password"
        hashed = _hash_password(plain)
        assert hashed != plain
        assert _verify_password(plain, hashed) is True

    def test_wrong_password_fails(self):
        hashed = _hash_password("correct")
        assert _verify_password("wrong", hashed) is False

    def test_different_hashes_for_same_password(self):
        """bcrypt はソルト付きなので同じパスワードでもハッシュが異なる。"""
        h1 = _hash_password("same")
        h2 = _hash_password("same")
        assert h1 != h2
        assert _verify_password("same", h1) is True
        assert _verify_password("same", h2) is True


# ---------------------------------------------------------------------------
# JWT token
# ---------------------------------------------------------------------------


class TestCreateToken:
    def test_token_decodes_correctly(self):
        token = _create_token("user-1", "taro", "taro@example.com", ROLE_STUDENT)
        payload = jwt.decode(token, "test-secret-key", algorithms=["HS256"])
        assert payload["sub"] == "user-1"
        assert payload["username"] == "taro"
        assert payload["email"] == "taro@example.com"
        assert payload["role"] == ROLE_STUDENT

    def test_default_role_is_student(self):
        token = _create_token("user-2", "hanako", "hanako@example.com")
        payload = jwt.decode(token, "test-secret-key", algorithms=["HS256"])
        assert payload["role"] == ROLE_STUDENT

    def test_teacher_role(self):
        token = _create_token("user-3", "sensei", "sensei@example.com", ROLE_TEACHER)
        payload = jwt.decode(token, "test-secret-key", algorithms=["HS256"])
        assert payload["role"] == ROLE_TEACHER

    def test_token_contains_exp(self):
        token = _create_token("user-4", "test", "test@example.com")
        payload = jwt.decode(token, "test-secret-key", algorithms=["HS256"])
        assert "exp" in payload


# ---------------------------------------------------------------------------
# Role mapping
# ---------------------------------------------------------------------------


class TestRoleMapping:
    def test_pg_to_app_role(self):
        assert _pg_role_to_app_role("learner") == ROLE_STUDENT
        assert _pg_role_to_app_role("instructor") == ROLE_TEACHER
        assert _pg_role_to_app_role("admin") == ROLE_SYSTEM_ADMIN

    def test_pg_to_app_role_unknown_defaults_to_student(self):
        assert _pg_role_to_app_role("unknown") == ROLE_STUDENT

    def test_app_to_pg_role(self):
        assert _app_role_to_pg_role(ROLE_STUDENT) == "learner"
        assert _app_role_to_pg_role(ROLE_TEACHER) == "instructor"
        assert _app_role_to_pg_role(ROLE_SYSTEM_ADMIN) == "admin"

    def test_app_to_pg_role_unknown_defaults_to_learner(self):
        assert _app_role_to_pg_role("UNKNOWN") == "learner"
