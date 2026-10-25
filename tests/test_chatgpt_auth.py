import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from phoneagent_core import chatgpt_auth as auth


class ChatGPTAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key()
        cls.jwk = jwt.algorithms.RSAAlgorithm.to_jwk(cls.public_key, as_dict=True)
        cls.jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        auth._pending.clear()
        auth._discovery_cache = (time.time() + 3600, {
            "issuer": auth.ISSUER,
            "jwks_uri": auth.ISSUER + "/.well-known/jwks.json",
            "authorization_endpoint": auth.AUTHORIZATION_ENDPOINT,
            "token_endpoint": auth.TOKEN_ENDPOINT,
            "revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke",
        })
        auth._jwks_cache = (time.time() + 3600, {"keys": [self.jwk]})

    def tearDown(self):
        self.temp.cleanup()

    def signed_id_token(self, client_id, *, sub="subject-1", nonce="", exp=None, aud=None):
        now = int(time.time())
        claims = {"iss": auth.ISSUER, "sub": sub, "aud": aud or client_id,
                  "iat": now, "exp": exp or now + 300, "nonce": nonce,
                  "email": "person@example.test"}
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "test-key"})

    def token_response(self, client_id, nonce, *, sub="subject-1", scope=None, exp=None, aud=None):
        return {"access_token": "opaque-access", "refresh_token": "opaque-refresh",
                "id_token": self.signed_id_token(client_id, sub=sub, nonce=nonce, exp=exp, aud=aud),
                "token_type": "Bearer", "expires_in": 3600,
                "scope": scope if scope is not None else auth.REQUESTED_SCOPES}

    def begin(self, account_id=None):
        return auth.begin_login(self.root, "http://127.0.0.1:8756/auth/callback", account_id)

    def tx_from_url(self, result):
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(result["url"]).query)
        state = query["state"][0]
        return state, auth._pending[state], query

    def complete(self, *, sub="subject-1", scope=None, callback_client=None,
                 exp=None, aud=None, nonce_override=None):
        started = self.begin()
        state, tx, query = self.tx_from_url(started)
        client_id = callback_client or "oaiapp_test-client"
        payload = self.token_response(client_id, nonce_override if nonce_override is not None else tx["nonce"],
                                      sub=sub, scope=scope, exp=exp, aud=aud)
        with mock.patch.object(auth, "_json_request", return_value=payload):
            result = auth.finish_login(self.root, {"code": "test-code", "state": state,
                                                   "client_id": client_id})
        return result, state, tx

    def test_pkce_callback_replay_and_safe_public_status(self):
        started = self.begin()
        state, tx, query = self.tx_from_url(started)
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("dynamic_agent_client", json.dumps(auth.account_status(self.root)))
        token_payload = self.token_response("oaiapp_test-client", tx["nonce"])
        with mock.patch.object(auth, "_json_request", return_value=token_payload) as request:
            public = auth.finish_login(self.root, {"code": "code", "state": state,
                                                   "client_id": "oaiapp_test-client"})
        form = request.call_args.args[1]
        self.assertEqual(form["code_verifier"], tx["verifier"])
        self.assertEqual(public["accounts"][0]["plan_enabled"], True)
        self.assertNotIn("opaque-access", json.dumps(public))
        self.assertNotIn("opaque-refresh", json.dumps(public))
        with self.assertRaises(auth.ChatGPTAuthError):
            auth.finish_login(self.root, {"code": "code", "state": state,
                                          "client_id": "oaiapp_test-client"})

    def test_csrf_wrong_state_does_not_complete_transaction(self):
        started = self.begin()
        state, tx, _ = self.tx_from_url(started)
        with self.assertRaises(auth.ChatGPTAuthError):
            auth.finish_login(self.root, {"code": "code", "state": "attacker-state",
                                          "client_id": "oaiapp_test-client"})
        response = self.token_response("oaiapp_test-client", tx["nonce"])
        with mock.patch.object(auth, "_json_request", return_value=response):
            self.assertTrue(auth.finish_login(self.root, {"code": "code", "state": state,
                                                          "client_id": "oaiapp_test-client"})["available"])

    def test_rejects_wrong_audience_nonce_and_expiration(self):
        cases = [("oaiapp_test-client", None, None, "other-client"),
                 ("oaiapp_test-client", None, "wrong-nonce", None),
                 ("oaiapp_test-client", int(time.time()) - 10, None, None)]
        for client, exp, nonce, aud in cases:
            started = self.begin()
            state, tx, _ = self.tx_from_url(started)
            response = self.token_response(client, nonce or tx["nonce"], exp=exp, aud=aud)
            with mock.patch.object(auth, "_json_request", return_value=response):
                with self.assertRaises(auth.ChatGPTAuthError):
                    auth.finish_login(self.root, {"code": "code", "state": state,
                                                  "client_id": client})

    def test_rejects_wrong_issued_client_and_identity_change(self):
        self.complete()
        account = auth.account_status(self.root)["accounts"][0]
        started = self.begin(account["id"])
        state, tx, _ = self.tx_from_url(started)
        with self.assertRaises(auth.ChatGPTAuthError):
            auth.finish_login(self.root, {"code": "code", "state": state,
                                          "client_id": "oaiapp-another"})

        started = self.begin(account["id"])
        state, tx, _ = self.tx_from_url(started)
        changed = self.token_response("oaiapp_test-client", tx["nonce"], sub="other-subject")
        with mock.patch.object(auth, "_json_request", return_value=changed):
            with self.assertRaises(auth.ChatGPTAuthError):
                auth.finish_login(self.root, {"code": "code", "state": state})

    def test_identity_only_sign_in_is_saved_but_not_available(self):
        result, _, _ = self.complete(scope="openid profile email")
        self.assertEqual(len(result["accounts"]), 1)
        self.assertFalse(result["accounts"][0]["plan_enabled"])
        self.assertTrue(result["available"])
        self.assertFalse(result["plan_ready"])
        with self.assertRaises(auth.ChatGPTAuthError):
            auth.access_token(self.root)

    def test_first_login_is_available_before_an_account_exists(self):
        status = auth.account_status(self.root)
        self.assertTrue(status["available"])
        self.assertFalse(status["plan_ready"])
        self.assertIsNone(status["active_id"])
        self.assertEqual(auth.models(self.root), [])

    def test_refresh_rotates_token_and_retains_scope_when_omitted(self):
        self.complete()
        with auth._locked(self.root):
            store = auth._load_store_unlocked(self.root)
            account = store["accounts"][0]
            account["expires_at"] = time.time() - 1
            account["models_cache"] = {"models": [{"slug": "admitted-model", "visibility": "list"}]}
            auth._save_store_unlocked(self.root, store)
        refresh_response = {"access_token": "new-access", "refresh_token": "new-refresh",
                            "token_type": "Bearer", "expires_in": 3600}
        with mock.patch.object(auth, "_json_request", return_value=refresh_response) as request:
            self.assertEqual(auth.access_token(self.root), "new-access")
        self.assertEqual(request.call_args.args[1]["grant_type"], "refresh_token")
        self.assertEqual(request.call_args.args[1]["refresh_token"], "opaque-refresh")
        with auth._locked(self.root):
            saved = auth._load_store_unlocked(self.root)["accounts"][0]
        self.assertEqual(saved["refresh_token"], "new-refresh")
        self.assertIn(auth.PLAN_SCOPE, saved["scopes"])
        self.assertEqual(auth.models(self.root), [{"slug": "admitted-model", "visibility": "list"}])

    def test_invalid_grant_clears_credentials_but_keeps_client_mapping(self):
        self.complete()
        with auth._locked(self.root):
            store = auth._load_store_unlocked(self.root)
            store["accounts"][0]["expires_at"] = time.time() - 1
            auth._save_store_unlocked(self.root, store)
        with mock.patch.object(auth, "_json_request", side_effect=auth._HTTPStatusError(400)):
            with self.assertRaises(auth.ChatGPTAuthError) as raised:
                auth.access_token(self.root)
        self.assertEqual(str(raised.exception), "The ChatGPT session expired. Sign in again.")
        with auth._locked(self.root):
            saved = auth._load_store_unlocked(self.root)["accounts"][0]
        self.assertEqual(saved["client_id"], "oaiapp_test-client")
        self.assertNotIn("refresh_token", saved)
        self.assertEqual(saved["scopes"], [])

    def test_models_uses_models_catalog_and_filters_list_visibility(self):
        self.complete()
        catalog = {"models": [
            {"slug": "visible-model", "display_name": "Visible", "visibility": "list"},
            {"slug": "hidden-model", "display_name": "Hidden", "visibility": "internal"},
        ]}
        with mock.patch.object(auth, "access_token", return_value="bearer-test") as token, \
             mock.patch.object(auth, "_json_request", return_value=catalog) as request:
            self.assertEqual(auth.models(self.root), [])
            request.assert_not_called()
            result = auth.models(self.root, refresh=True)
            self.assertEqual(auth.models(self.root), result)
        self.assertEqual(result, [catalog["models"][0]])
        self.assertEqual(token.call_count, 1)
        self.assertEqual(request.call_args.args[0], auth.MODELS_URL)
        self.assertEqual(request.call_args.kwargs["bearer"], "bearer-test")

    def test_logout_clears_plan_readiness_and_retains_registration(self):
        self.complete()
        account_id = auth.account_status(self.root)["active_id"]
        with mock.patch.object(auth, "_discovery", return_value={"revocation_endpoint": ""}):
            status = auth.logout(self.root, account_id)
        self.assertFalse(status["plan_ready"])
        self.assertFalse(status["accounts"][0]["plan_enabled"])
        self.assertEqual(status["accounts"][0]["id"], account_id)
        self.assertFalse(status["revocation_confirmed"])

    def test_http_requests_reject_non_official_origins_and_ports(self):
        for url in ("http://127.0.0.1:8756/auth/callback", "https://auth.openai.com:444/path",
                    "https://user@api.openai.com/v1/models"):
            with self.subTest(url=url), self.assertRaises(auth.ChatGPTAuthError):
                auth._http_request(url)

    def test_storage_and_host_id_permissions(self):
        self.begin()
        self.complete()
        for name in ("chatgpt_accounts.json", "chatgpt_hostid"):
            mode = Path(self.root / ".phoneagent_auth" / name).stat().st_mode & 0o777
            if auth.os.name != "nt":
                self.assertEqual(mode, 0o600)
        if auth.os.name != "nt":
            self.assertEqual((self.root / ".phoneagent_auth").stat().st_mode & 0o777, 0o700)
        self.assertIn("urn:uuid:", (self.root / ".phoneagent_auth" / "chatgpt_hostid").read_text())


if __name__ == "__main__":
    unittest.main()
