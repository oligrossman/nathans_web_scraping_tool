"""Lightweight username/password gate for the Streamlit app.

Security notes:
- The plaintext password is never stored. We keep only a PBKDF2-HMAC-SHA256
  hash plus its random salt, and re-derive the hash from the entered password to
  compare in constant time (``hmac.compare_digest``), which avoids timing leaks.
- Credentials can be overridden at deploy time via Streamlit secrets (see
  ``[auth]`` in ``.streamlit/secrets.toml`` or the Streamlit Cloud secrets UI),
  so rotating them never needs a code change. The baked-in values are only a
  convenient default — and being a one-way hash, they reveal nothing usable.
"""

from __future__ import annotations

import hashlib
import hmac

import streamlit as st

# Default credentials. The hash below is PBKDF2-HMAC-SHA256 of the password with
# the salt and iteration count shown — the password itself is not recoverable.
_DEFAULT_USERNAME = "wagahq"
_DEFAULT_SALT_HEX = "e54fe68e8cd05f4e4e9afa0764d7a526"
_DEFAULT_HASH_HEX = "a80b5bd98d8b36a1f5867d2d801b506ea7f7967b50d039a86dd8e0adb113496b"
_ITERATIONS = 200_000

_AUTH_FLAG = "authenticated"


def _hash_password(password: str, salt: bytes) -> bytes:
    """Derive the slow PBKDF2 hash of a password with the given salt."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)


def _expected_credentials() -> tuple[str, bytes, bytes]:
    """Return ``(username, salt, password_hash)`` from secrets or the defaults."""
    try:
        creds = st.secrets["auth"]
        return (
            str(creds["username"]),
            bytes.fromhex(str(creds["salt"])),
            bytes.fromhex(str(creds["password_hash"])),
        )
    except Exception:
        return (
            _DEFAULT_USERNAME,
            bytes.fromhex(_DEFAULT_SALT_HEX),
            bytes.fromhex(_DEFAULT_HASH_HEX),
        )


def verify_credentials(username: str, password: str) -> bool:
    """Constant-time check of a username/password pair against the stored hash."""
    exp_user, salt, exp_hash = _expected_credentials()
    # Compare both fields with compare_digest so a wrong username can't be
    # distinguished from a wrong password by response timing.
    user_ok = hmac.compare_digest(username.strip(), exp_user)
    pass_ok = hmac.compare_digest(_hash_password(password, salt), exp_hash)
    return user_ok and pass_ok


def _render_login() -> None:
    """Draw the sign-in form and authenticate on submit."""
    st.title("Sign in")
    st.caption("Enter your credentials to access the platform.")
    with st.form("login_form"):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input(
            "Password", type="password", autocomplete="current-password"
        )
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if verify_credentials(username, password):
            st.session_state[_AUTH_FLAG] = True
            st.rerun()
        else:
            st.error("Invalid username or password.")


def require_login() -> None:
    """Gate the app: show the login form and halt unless already signed in."""
    # if st.session_state.get(_AUTH_FLAG):
    #     return
    _render_login()
    st.stop()


def logout_button() -> None:
    """Render a sidebar sign-out control that clears the session."""
    if st.sidebar.button("Sign out"):
        st.session_state[_AUTH_FLAG] = False
        st.rerun()
