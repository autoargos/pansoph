"""Narrow, typed errors for the OAuth connect subsystem.

Thiserror-style: each error is a distinct type with a precise message so a caller
(a route handler) can map it to the right HTTP status without string matching. The
base type `OAuthError` catches the whole family. No em dashes anywhere.
"""

from __future__ import annotations


class OAuthError(Exception):
    """Base class for every error raised by the OAuth connect subsystem."""


class UnknownProviderError(OAuthError):
    """The named provider is not registered.

    Raised when a route references a provider slug (google, slack, atlassian,
    github) that has no registered `OAuthProvider`. Maps to HTTP 404.
    """


class ProviderNotConfiguredError(OAuthError):
    """The provider is known but its client id or secret is not configured.

    The operator has not set the provider's client credentials in the environment
    or the secret store, so no consent flow can start. Maps to HTTP 400 with a
    message naming the missing environment variables.
    """


class StateError(OAuthError):
    """The OAuth `state` value is missing, unknown, expired, or already used.

    A returned `state` that does not match a live, single-use record is treated as
    a forged or replayed callback and rejected. Maps to HTTP 400. This is the CSRF
    guard for the callback, so it never silently trusts an unverified state.
    """


class CodeExchangeError(OAuthError):
    """Exchanging the authorization code for tokens failed.

    The provider's token endpoint returned a non-2xx response, an error body, or a
    response with no access token. Maps to HTTP 502: the failure is upstream, not
    the caller's fault.
    """
