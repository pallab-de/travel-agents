# Security policy

## Reporting a vulnerability

Please report security problems privately. Do not open a public issue, pull request or discussion that
describes the problem.

- Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability").
- If that is unavailable, contact the maintainer through the contact details on their GitHub profile and ask for a
  private channel first.

Include the affected file or command, the steps to reproduce, and the impact you expect. The maintainer will
acknowledge the report, work on a fix, and credit you in the release notes if you wish.

## Leaked keys and credentials

If you find a real API key, token, password or booking reference in this repository, its history, an issue or a
pull request:

1. Report it privately as above. Do not paste the value in a public place, including the report body if the
   channel is not private.
2. Revoke or rotate the credential at its provider immediately. Assume it is compromised as soon as it has been
   pushed anywhere public.
3. Do not try to "clean it up" by editing a public issue or pull request. Rotation is what makes it safe.

## Keeping secrets out of the repository

- API keys belong only in the encrypted store: `secrets/.env.enc` and `secrets/.env.key`. The `secrets/` folder
  is ignored by `.gitignore`.
- The encryption is symmetric and **not user- or machine-specific**: the key file sits beside the encrypted file, so anyone who obtains both can decrypt them.
  Keep the folder out of cloud-sync folders (OneDrive, Dropbox, iCloud Drive, Google Drive); the setup check warns when it is in one. Point `TRAVEL_SECRETS_DIR`
  at a per-user local folder, or in cloud sessions and CI supply the key as an environment variable of the same name, which is used before the store.
- Plaintext `.env` files must not be committed. `encrypt_env.py` deletes the plaintext after it verifies the
  encrypted copy.
- Check before you commit: `git status` should not list `secrets/`, `.env`, `*.key` or `*.enc` files.
- Run `python tools/privacy_scan.py --install-hooks` once. It installs a pre-commit and a pre-push check that fail if a commit or
  the history carries the real value of any key in your encrypted stores, a key-shaped string (Google, OpenAI-style, GitHub, Apify,
  AWS, Slack), a private-key block, an e-mail address, an absolute user path, or any private term you list (your e-mail, phone,
  home address, full name) in `.git/privacy-terms.txt`, a file inside `.git` that is never pushed. It never prints a value.

## Scope

This policy covers the code and scripts in this repository. It does not cover the third-party services the
scripts call (Google Maps Platform, listing sites, review providers), which have their own security policies.
