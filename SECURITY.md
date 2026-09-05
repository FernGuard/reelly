# Security

## API keys

Reelly does not ship API keys. Never put keys, tokens, or credentials in this
repository, in issues, or in pull requests.

Store keys in the environment or in `~/.reelly/config.json` (file mode 0600).
That path is outside the repo. `.env` is gitignored.

If a command is missing a key it will name the environment variable and the
signup URL. It will not print the key.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability). Do not open a public issue for a
suspected secret leak or exploit.

If you find a key or credential in git history, report it privately and rotate
the key immediately.

## Sensitive media and personal data

Security reports must not attach production footage, transcripts, source URLs,
client names, or analytics. Use a synthetic reproduction. Review
[DATA_AND_PRIVACY.md](DATA_AND_PRIVACY.md) before enabling cloud providers.
