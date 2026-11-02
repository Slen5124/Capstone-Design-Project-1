# Public repository data policy

Publish code, synthetic examples, and reviewed public summaries only.

- Keep customer budgets, actual costs, original schedules, site images, reports,
  institution-only manuals, and credentials in approved private storage.
- Use placeholders such as `<GPU_HOST>`, `<SSH_PORT>`, `<GPU_USER>`, and
  `<GPU_NODE>` in public server instructions. Do not publish live connection details.
- Review and anonymize meeting notes before publishing. Do not synchronize
  private meeting minutes verbatim into this repository.
- Check every staged file, including PDFs, screenshots, metadata, and generated files.
  `.gitignore` does not remove files that were already committed.

## After the 2026-11-02 history cleanup

Public branch history was rewritten to remove restricted information.
Keep existing local work and original data as private backups. Do not merge or
force-push the old history into the cleaned repository.

Create a fresh clone in a separate folder for future work. Move only reviewed,
necessary source changes into that clone and inspect the diff before committing.
Do not upload old Git bundles, private originals, or maintenance audit artifacts.

GitHub-managed PR references and cached old commits may require a separate
GitHub Support cleanup. Rewriting branch history alone does not prove that all
server-side copies have been purged.

## Public demonstration exception

`prototype/public-demo/` contains the existing reviewed public demonstration:
fictional data and demonstration assets, never a destination for customer uploads.
Only the individually listed source assets and HTML templates are allowlisted in
`.gitignore`. Generated `dist/`, user uploads, real reports, photos and credentials
remain excluded. Review any replacement asset before publication.
