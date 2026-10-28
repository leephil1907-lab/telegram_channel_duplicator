# Relay 0.8.1: review, Telegram sign-in and deployment

This update is based on `leephil1907-lab/telegram_channel_duplicator` commit `0f0598a`
on `main`. The supplied `NotStatilko/tgback` repository was reviewed as a reference.
Check the GitHub branch and Render dashboard for the actual revision and deployment status;
a Git push alone does not prove that Render has finished deploying.

## What was built

- Six reloadable, responsive routes preserve the channel-mirroring workspace, including
  browser navigation, configuration, live worker status, search, activity and themes.
- `/recovery` is now an operational recovery center for signed-in workspaces, using a
  separate Telethon session: sign-in and 2FA challenges, encrypted file/QR export,
  Telegram-verified restoration, session verification, official Telegram service-message
  retrieval, phone-change request and confirmation, scheduled Saved Messages reminders,
  disconnect and revocation. One recovery session is held per Relay workspace; use a
  separate Relay account for another Telegram account. No operation auto-starts and no
  sample data is presented.
- Relay-password reauthentication and explicit confirmation protect sensitive actions;
  revocation and phone change also require typed confirmation. Failed password attempts
  have a process-local per-account limit. Secrets and service-message bodies are not
  stored in the activity log. Results clear from the browser after a short interval.
- Backups contain an encrypted Telegram session and API credentials, **not message
  history**. Relay uses AES-256-GCM, scrypt and validated JSON. It does not deserialize
  pickle, and **does not support importing legacy `.tgback` files**. Imported session
  endpoints are replaced with data-center options obtained from Telegram, preventing an
  uploaded address from being used as an arbitrary network destination.
- The active session and saved API credentials remain **unencrypted private files on the
  server**. Restrict server administrators, encrypt disk backups and use HTTPS. A server
  administrator can access these files. This is not an independently audited product.
- Both hosted sign-in paths request a real Telegram code, display supported delivery
  metadata, and ask for the Telegram two-step password only if Telegram requires it.
  Recovery can resend with an active challenge ID and cooldown. Telegram selects delivery,
  and saved sessions may reconnect without a code. Unsupported email setup, Firebase SMS
  and paid verification are reported instead of presented as successful.
- Mirroring sessions are isolated per phone number, matching legacy sessions are reused,
  and the authorized account is checked before use. Worker status stays Starting until
  authentication and channel resolution succeed. Safe dry runs, confirmed actions,
  atomic configuration and fail-closed chat resolution remain. Exactly-once delivery
  is not guaranteed.

## Verification

- **108 automated tests passed** on Python 3.13, including recovery encryption and QR,
  API authorization, workspace isolation, invalid/expired codes, conditional two-step
  verification, resend, email code, mismatched accounts and worker readiness. The full
  suite also passed after a clean dependency installation with `pip check`.
- The authentication tests execute actual Telethon 1.45 and 1.34 send/resend/sign-in
  methods, but substitute a **test-only MTProto RPC boundary**. Browser tests exercised
  mobile and desktop workflows, including a UI-to-HTTP-to-Telethon authentication flow
  through that test boundary, including both recovery and the actual mirroring worker.
  This verifies integration, not Telegram delivery. No test mode or simulated user-facing
  feature is shipped.
- **No real Telegram credentials were available.** Live code delivery, authentication,
  message retrieval, phone changes, reminders, revocation and forwarding have not been
  verified against Telegram. Telegram can choose a different delivery method, impose
  additional waits or require an unsupported official-app step. A Docker build was not
  run in the development sandbox; verify the Render deployment separately. The
  application has not been independently security-audited.

## Manual Telegram acceptance check

Use your own account, API credentials and a trusted HTTPS deployment. Enter secrets only
in that server's UI—never in chat. An existing authorized session may skip the code step;
do not revoke a valuable session merely to force a test.

1. Open **Recovery center → Sign in to Telegram** and enter your international phone
   number, API ID and hash. Confirm **Request sign-in code**. Check that Telegram actually
   delivers the code by its chosen method; enter it. If two-step verification is enabled,
   confirm that the next prompt is for the Telegram two-step password (not Relay's
   password) and that the displayed account is yours after connecting.
2. Where Telegram permits it, test one incorrect code and **Request another code** after
   the displayed wait. Telegram may impose longer waits or decline a resend. Verify that
   an expired code reports the need to reconnect instead of claiming success. Do not
   repeatedly request codes or passwords, which can trigger Telegram restrictions.
3. Save a mirroring configuration for an account with access to your own source and
   destination chats. Start in **Dry run** and complete any Telegram login prompt. Check
   that status remains Starting until the correct account and chats are resolved; verify
   no messages are sent in dry-run mode. Only enable live forwarding after reviewing the
   activity and permissions. If available, separately test an account without two-step
   verification to confirm it does not receive a password prompt.
4. Encrypt and securely store a Relay backup; verify restoration with Telegram only when
   you understand that backups grant account access and a revoked session cannot be
   recovered. Recovery does not bypass Telegram's account security and cannot guarantee
   recovery when no valid session, phone access or required password is available.

## Deploy and verify

If this update is present on the repository's `main` branch, pull that branch and run the
checks locally:

```sh
git checkout main
git pull --ff-only
python -m pip install -e '.[dev]'
python -m pytest
```

An optional `relay-upgrade.patch` may be applied only to a clean checkout of the original
base commit `0f0598a`; do not apply it again to a branch that already contains the update.
Resolve conflicts rather than forcing the patch. The source archive is an alternative.

If the existing Render service is connected to this repository and branch with automatic
deploys enabled, a successful push can trigger a deployment. Confirm the specific commit
and status in Render's dashboard, inspect deploy logs, and check `/api/health` and the
`/recovery` page. A healthy endpoint alone does not prove the new commit is deployed.
Keep the persistent disk mounted at `/app/data`, use HTTPS and secure cookies, restrict
registration/access, and add reverse-proxy rate limiting. Run only one application
instance per data directory. Password attempt limits are in memory, not distributed, and
reset at restart. No built-in Relay password recovery exists.

After deployment, sign in on your own trusted HTTPS server, supply your own Telegram API
credentials privately in the UI, and test with your own account. Do not send passwords,
login codes, API hashes or session files to anyone in chat or support. A recovery backup
and its password together give full account access. Revocation invalidates every backup of
that session; Telegram may also revoke sessions independently.
