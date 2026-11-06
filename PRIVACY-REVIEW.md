# Publication privacy review

Prepared 6 November 2026 from an explicit file allow-list.

- Private `.env`, Streamlit secrets, runtime model stores, local instance records and Git credential data are excluded.
- The staged files were checked against known local API keys, bot tokens and database passwords, plus common credential signatures.
- Personal Windows home-directory paths in recorded test logs were replaced with `[user-profile]`.
- The initial publication uses GitHub's privacy email and contains no inherited local Git history.
- Any email-like strings remaining in tests use example domains and are synthetic fixtures.
- Study questions, student labels and game characters in evidence are scripted examples, not actual student/player records.
- Original development folders and their private configuration were not copied wholesale or modified by publication.

Automated scanning reduces risk but cannot prove that every arbitrary string is non-sensitive. New uploads and future logs should be reviewed again.
