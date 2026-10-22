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
- Plaintext `.env` files must not be committed. `encrypt_env.py` deletes the plaintext after it verifies the
  encrypted copy.
- Check before you commit: `git status` should not list `secrets/`, `.env`, `*.key` or `*.enc` files.

## Scope

This policy covers the code and scripts in this repository. It does not cover the third-party services the
scripts call (Google Maps Platform, listing sites, review providers), which have their own security policies.
