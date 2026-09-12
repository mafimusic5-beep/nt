from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch point: {label}")
    return text.replace(old, new, 1)


def finalize_recovery() -> None:
    target = Path("orchestrator/device_recovery_routes.py")
    text = target.read_text(encoding="utf-8")

    schema_old = """            new_key_fingerprint TEXT NOT NULL,\n            challenge_hash TEXT NOT NULL,\n"""
    schema_new = """            new_key_fingerprint TEXT NOT NULL,\n            old_key_fingerprint TEXT NOT NULL DEFAULT '',\n            challenge_hash TEXT NOT NULL,\n"""
    text = replace_once(text, schema_old, schema_new, "recovery schema")

    index_old = """    con.execute(\n        \"CREATE INDEX IF NOT EXISTS idx_device_recovery_expiry ON device_recovery_challenges(expires_at_epoch)\"\n    )\n"""
    index_new = """    columns = {str(row[\"name\"]) for row in con.execute(\"PRAGMA table_info(device_recovery_challenges)\").fetchall()}\n    if \"old_key_fingerprint\" not in columns:\n        con.execute(\n            \"ALTER TABLE device_recovery_challenges ADD COLUMN old_key_fingerprint TEXT NOT NULL DEFAULT ''\"\n        )\n    con.execute(\n        \"CREATE INDEX IF NOT EXISTS idx_device_recovery_expiry ON device_recovery_challenges(expires_at_epoch)\"\n    )\n"""
    text = replace_once(text, index_old, index_new, "recovery migration")

    insert_old = """                    new_key_fingerprint,\n                    challenge_hash,\n                    request_hash,\n                    created_at_epoch,\n                    expires_at_epoch\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n"""
    insert_new = """                    new_key_fingerprint,\n                    old_key_fingerprint,\n                    challenge_hash,\n                    request_hash,\n                    created_at_epoch,\n                    expires_at_epoch\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n"""
    text = replace_once(text, insert_old, insert_new, "recovery challenge insert columns")

    values_old = """                    requested_device_id,\n                    new_key_fingerprint,\n                    challenge_hash,\n                    request_hash,\n"""
    values_new = """                    requested_device_id,\n                    new_key_fingerprint,\n                    old_key_fingerprint,\n                    challenge_hash,\n                    request_hash,\n"""
    text = replace_once(text, values_old, values_new, "recovery challenge insert values")

    marker = '@router.post("/confirm")\ndef recovery_confirm(payload: RecoveryConfirmRequest):\n'
    if marker not in text:
        raise SystemExit("missing recovery confirm marker")
    prefix = text.split(marker, 1)[0]
    replacement = r'''def _validate_confirm_challenge(
    row: sqlite3.Row | None,
    *,
    code: str,
    requested_device_id: str,
    new_key_fingerprint: str,
    server_challenge: str,
    now_epoch: int,
) -> None:
    if not row:
        raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
    if row["consumed_at_epoch"] is not None:
        raise device_auth.DeviceAuthError("device_recovery_replay_detected", 401)
    if int(row["expires_at_epoch"] or 0) < now_epoch:
        raise device_auth.DeviceAuthError("device_recovery_challenge_expired", 401)
    if str(row["code"]) != code:
        raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
    if str(row["requested_device_id"]) != requested_device_id:
        raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)
    if not hmac.compare_digest(str(row["new_key_fingerprint"]), new_key_fingerprint):
        raise device_auth.DeviceAuthError("device_recovery_key_mismatch", 401)
    supplied_challenge_hash = hashlib.sha256(server_challenge.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(str(row["challenge_hash"]), supplied_challenge_hash):
        raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)


@router.post("/confirm")
def recovery_confirm(payload: RecoveryConfirmRequest):
    requested_device_id = payload.device_id.strip().lower()
    if not DEVICE_PROBE_RE.fullmatch(requested_device_id):
        return _json_error("device_recovery_probe_invalid", 400)
    if _rate_limited(payload.code, requested_device_id):
        return _json_error("too_many_attempts", 429)

    try:
        device_auth._check_timestamp_with_skew(payload.timestamp, 120)
        _, new_key_fingerprint = device_auth._decode_public_key(payload.client_public_key)
        confirm_canonical = _confirm_canonical(
            challenge_id=payload.challenge_id,
            server_challenge=payload.server_challenge,
            device_id=requested_device_id,
            new_key_fingerprint=new_key_fingerprint,
            code=payload.code,
            timestamp=payload.timestamp,
            nonce=payload.nonce,
            integrity_token=payload.integrity_token,
        )
        verified_fingerprint = device_auth._verify_signature(
            payload.client_public_key,
            payload.signature,
            confirm_canonical,
            payload.signature_algorithm,
        )
        if not hmac.compare_digest(verified_fingerprint, new_key_fingerprint):
            raise device_auth.DeviceAuthError("device_signature_invalid", 401)

        # Phase 1: verify immutable challenge state without keeping a SQLite
        # write lock while the remote Play Integrity API is contacted.
        con = _connect()
        try:
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code = str(activation["code"])
            row = con.execute(
                "SELECT * FROM device_recovery_challenges WHERE challenge_id = ?",
                (payload.challenge_id.strip(),),
            ).fetchone()
            _validate_confirm_challenge(
                row,
                code=code,
                requested_device_id=requested_device_id,
                new_key_fingerprint=new_key_fingerprint,
                server_challenge=payload.server_challenge,
                now_epoch=int(time.time()),
            )
            expected_request_hash = str(row["request_hash"])
            con.commit()
        except device_auth.DeviceAuthError as error:
            if error.reason == "expired":
                con.commit()
            else:
                con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

        try:
            play_integrity.verify_standard_token(payload.integrity_token, expected_request_hash)
        except play_integrity.PlayIntegrityError as exc:
            reason = str(exc)
            transient = reason in {
                "play_integrity_not_configured",
                "play_integrity_credentials_invalid",
                "play_integrity_oauth_failed",
                "play_integrity_decode_failed",
            }
            raise device_auth.DeviceAuthError(reason, 503 if transient else 403) from exc

        # Phase 2: re-check everything under one write transaction. Only this
        # transaction can consume the challenge and replace the registered key.
        con = _connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            _ensure_storage(con)
            activation = device_auth._activation_row(con, payload.code)
            code_after_integrity = str(activation["code"])
            if code_after_integrity != code:
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)

            row = con.execute(
                "SELECT * FROM device_recovery_challenges WHERE challenge_id = ?",
                (payload.challenge_id.strip(),),
            ).fetchone()
            now_epoch = int(time.time())
            _validate_confirm_challenge(
                row,
                code=code,
                requested_device_id=requested_device_id,
                new_key_fingerprint=new_key_fingerprint,
                server_challenge=payload.server_challenge,
                now_epoch=now_epoch,
            )
            if not hmac.compare_digest(str(row["request_hash"]), expected_request_hash):
                raise device_auth.DeviceAuthError("device_recovery_challenge_invalid", 401)

            device = con.execute(
                """
                SELECT id, device_id, public_key, public_key_fingerprint, active, pool_assignment_id
                FROM code_devices
                WHERE id = ? AND code = ?
                """,
                (int(row["device_row_id"]), code),
            ).fetchone()
            if not device or not bool(device["active"]):
                raise device_auth.DeviceAuthError("device_revoked", 403)

            current_device_id = str(device["device_id"] or "")
            if current_device_id not in {str(row["old_device_id"]), requested_device_id}:
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)

            expected_old_key = str(row["old_key_fingerprint"] or "").strip()
            current_old_key = _stored_key_fingerprint(device)
            if expected_old_key and not hmac.compare_digest(expected_old_key, current_old_key):
                raise device_auth.DeviceAuthError("device_recovery_conflict", 409)

            device_auth._consume_nonce(
                con,
                code=code,
                device_id=requested_device_id,
                nonce=payload.nonce,
            )
            _migrate_device_id_if_needed(
                con,
                code=code,
                row=device,
                requested_device_id=requested_device_id,
            )
            con.execute(
                """
                UPDATE code_devices
                SET public_key = ?,
                    public_key_fingerprint = ?,
                    last_seen_at = ?,
                    active = 1
                WHERE id = ?
                """,
                (
                    payload.client_public_key,
                    new_key_fingerprint,
                    now_iso(),
                    int(device["id"]),
                ),
            )
            updated = con.execute(
                """
                UPDATE device_recovery_challenges
                SET consumed_at_epoch = ?
                WHERE challenge_id = ? AND consumed_at_epoch IS NULL
                """,
                (now_epoch, payload.challenge_id.strip()),
            )
            if updated.rowcount != 1:
                raise device_auth.DeviceAuthError("device_recovery_replay_detected", 401)
            con.commit()
            return {
                "ok": True,
                "status": "recovered",
                "recovered": True,
                "integrity_required": True,
            }
        except device_auth.DeviceAuthError as error:
            if error.reason == "expired":
                con.commit()
            else:
                con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    except device_auth.DeviceAuthError as exc:
        return _json_error(exc.reason, exc.status_code)
    except Exception:
        return _json_error("device_recovery_failed", 500)
'''
    target.write_text(prefix + replacement, encoding="utf-8")


def extend_recovery_tests() -> None:
    target = Path("orchestrator/tests/test_device_recovery.py")
    text = target.read_text(encoding="utf-8")
    if "import hmac\n" not in text:
        text = replace_once(text, "import hashlib\n", "import hashlib\nimport hmac\n", "recovery test hmac import")

    marker = "\n\nif __name__ == '__main__':\n"
    if marker not in text:
        raise SystemExit("missing recovery test insertion marker")
    extra = r'''

    def test_integrity_failure_keeps_old_key_and_challenge_unconsumed(self) -> None:
        code = self.create_code()
        device_id = self.probe('0123456789abcdef')
        old_key = self.new_private_key()
        new_key = self.new_private_key()
        self.register(code=code, device_id=device_id, key=old_key)

        status, challenge = self.response_payload(
            device_recovery_routes.recovery_challenge(
                self.challenge_payload(code=code, device_id=device_id, key=new_key)
            )
        )
        self.assertEqual(200, status)

        def reject_integrity(token, expected_hash):
            raise play_integrity.PlayIntegrityError('play_integrity_device_failed')
        play_integrity.verify_standard_token = reject_integrity

        request = self.confirm_payload(
            code=code,
            device_id=device_id,
            key=new_key,
            challenge=challenge,
            token='rejected-integrity-token-' + uuid.uuid4().hex,
        )
        status, body = self.response_payload(device_recovery_routes.recovery_confirm(request))
        self.assertEqual(403, status)
        self.assertEqual('play_integrity_device_failed', body['reason'])

        profile = self.authenticate(code=code, device_id=device_id, key=old_key)
        self.assertEqual(device_id, profile['device_id'])
        with sqlite3.connect(self.db_path) as con:
            consumed = con.execute(
                'SELECT consumed_at_epoch FROM device_recovery_challenges WHERE challenge_id = ?',
                (challenge['challenge_id'],),
            ).fetchone()[0]
        self.assertIsNone(consumed)

    def test_legacy_pool_subject_alias_is_preserved_during_id_scrub(self) -> None:
        code = self.create_code()
        legacy_android_id = '0123456789abcdef'
        device_id = self.probe(legacy_android_id)
        old_key = self.new_private_key()
        self.register(code=code, device_id=legacy_android_id, key=old_key)

        original_secret = device_identity_aliases.POOL_BRIDGE_PSEUDONYM_KEY
        device_identity_aliases.POOL_BRIDGE_PSEUDONYM_KEY = 'test-pool-pseudonym-secret'
        try:
            with sqlite3.connect(self.db_path) as con:
                con.execute(
                    'UPDATE code_devices SET pool_assignment_id = 4242 WHERE code = ? AND device_id = ?',
                    (code, legacy_android_id),
                )
                con.commit()

            status, body = self.response_payload(
                device_recovery_routes.recovery_challenge(
                    self.challenge_payload(code=code, device_id=device_id, key=old_key)
                )
            )
            self.assertEqual(200, status)
            self.assertEqual('migrated', body['status'])

            expected = hmac.new(
                b'test-pool-pseudonym-secret',
                ('legacy-device-v1\0' + code + '\0' + legacy_android_id).encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            with sqlite3.connect(self.db_path) as con:
                row = con.execute(
                    'SELECT subject_key FROM device_pool_subject_aliases WHERE code = ? AND device_id = ?',
                    (code, device_id),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(expected, row[0])
        finally:
            device_identity_aliases.POOL_BRIDGE_PSEUDONYM_KEY = original_secret
'''
    target.write_text(text.replace(marker, extra + marker, 1), encoding="utf-8")


def add_vpn_disclosure() -> None:
    disclosure = Path("app/src/main/java/com/v2ray/ang/security/SkryonVpnDisclosure.kt")
    disclosure.write_text(
        '''package com.v2ray.ang.security\n\nimport android.app.Activity\nimport android.app.AlertDialog\nimport android.content.Intent\nimport android.net.Uri\n\nobject SkryonVpnDisclosure {\n\n    private const val PREFS = "skryon_privacy"\n    private const val ACCEPTED_KEY = "vpn_disclosure_v1_accepted"\n    private const val PRIVACY_URL = "https://skryon.ru/privacy.html"\n\n    fun showIfNeeded(\n        activity: Activity,\n        onAccepted: () -> Unit,\n        onDeclined: () -> Unit = {},\n    ) {\n        val preferences = activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE)\n        if (preferences.getBoolean(ACCEPTED_KEY, false)) {\n            onAccepted()\n            return\n        }\n\n        var completed = false\n        fun declineOnce() {\n            if (!completed) {\n                completed = true\n                onDeclined()\n            }\n        }\n\n        val dialog = AlertDialog.Builder(activity)\n            .setTitle("VPN-подключение Skryon")\n            .setMessage(\n                "Skryon использует Android VpnService для создания зашифрованного " +\n                    "VPN-туннеля между устройством и выбранным VPN-сервером. Во время " +\n                    "подключения сетевой трафик устройства технически проходит через " +\n                    "выбранный VPN-сервер. Skryon не использует VpnService для рекламного " +\n                    "отслеживания или монетизации трафика и не сохраняет историю посещённых " +\n                    "сайтов или содержимое VPN-трафика в VPN access-логах. Нажимая " +\n                    "«Продолжить», вы разрешаете Skryon использовать VpnService для " +\n                    "создания VPN-подключения."\n            )\n            .setPositiveButton("Продолжить") { _, _ ->\n                completed = true\n                preferences.edit().putBoolean(ACCEPTED_KEY, true).apply()\n                onAccepted()\n            }\n            .setNegativeButton("Отмена") { _, _ -> declineOnce() }\n            .setNeutralButton("Политика конфиденциальности", null)\n            .setOnCancelListener { declineOnce() }\n            .create()\n\n        dialog.setOnShowListener {\n            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {\n                runCatching {\n                    activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(PRIVACY_URL)))\n                }\n            }\n        }\n        dialog.show()\n    }\n}\n''',
        encoding="utf-8",
    )

    main = Path("app/src/main/java/com/v2ray/ang/ui/MainActivity.kt")
    text = main.read_text(encoding="utf-8")
    import_point = "import com.v2ray.ang.handler.V2RayServiceManager\n"
    if "import com.v2ray.ang.security.SkryonVpnDisclosure\n" not in text:
        text = replace_once(
            text,
            import_point,
            import_point + "import com.v2ray.ang.security.SkryonVpnDisclosure\n",
            "MainActivity disclosure import",
        )
    old = '''        } else if (SettingsManager.isVpnMode()) {\n            val intent = VpnService.prepare(this)\n            if (intent == null) {\n                startV2Ray()\n            } else {\n                requestVpnPermission.launch(intent)\n            }\n        } else {\n'''
    new = '''        } else if (SettingsManager.isVpnMode()) {\n            SkryonVpnDisclosure.showIfNeeded(\n                activity = this,\n                onAccepted = {\n                    val intent = VpnService.prepare(this)\n                    if (intent == null) {\n                        startV2Ray()\n                    } else {\n                        requestVpnPermission.launch(intent)\n                    }\n                },\n                onDeclined = { applyRunningState(isLoading = false, isRunning = false) },\n            )\n        } else {\n'''
    text = replace_once(text, old, new, "MainActivity VpnService disclosure")
    main.write_text(text, encoding="utf-8")

    premium = Path("app/src/main/java/com/v2ray/ang/ui/premium/PremiumActivity.kt")
    text = premium.read_text(encoding="utf-8")
    import_point = "import com.v2ray.ang.handler.V2RayServiceManager\n"
    if "import com.v2ray.ang.security.SkryonVpnDisclosure\n" not in text:
        text = replace_once(
            text,
            import_point,
            import_point + "import com.v2ray.ang.security.SkryonVpnDisclosure\n",
            "PremiumActivity disclosure import",
        )
    old = '''                    requestVpnPermission = { onGranted ->\n                        val intent = VpnService.prepare(this)\n                        if (intent == null) {\n                            onGranted()\n                        } else {\n                            onVpnPermissionGranted = onGranted\n                            vpnPermissionLauncher.launch(intent)\n                        }\n                    },\n'''
    new = '''                    requestVpnPermission = { onGranted ->\n                        SkryonVpnDisclosure.showIfNeeded(\n                            activity = this,\n                            onAccepted = {\n                                val intent = VpnService.prepare(this)\n                                if (intent == null) {\n                                    onGranted()\n                                } else {\n                                    onVpnPermissionGranted = onGranted\n                                    vpnPermissionLauncher.launch(intent)\n                                }\n                            },\n                        )\n                    },\n'''
    text = replace_once(text, old, new, "PremiumActivity VpnService disclosure")
    premium.write_text(text, encoding="utf-8")


def main() -> None:
    finalize_recovery()
    extend_recovery_tests()
    add_vpn_disclosure()


if __name__ == "__main__":
    main()
