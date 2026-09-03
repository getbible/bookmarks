# Deployment

One Linux host runs nginx for the static API and the management service. The
repository is the deployment unit: `deploy/install.sh` sets the host up and
`deploy/deploy.sh` publishes the checked-out branch.

## Host layout

```text
/srv/getbible-bookmarks/
  repo/       git checkout of getbible/bookmarks on `main` (service user, read-write)
  www/        release root served by nginx
    releases/<checksum>/v1/…   immutable trees with .gz siblings
    current -> releases/<checksum>   atomically switched symlink
  venv/       Python virtualenv with the hashed runtime lock
/etc/getbible-bookmarks/
  env                 environment for both units (root:getbible-bookmarks, 0640)
  contributors.json   token registry (getbible-bookmarks, 0600)
  deploy_key          SSH deploy key with write access (getbible-bookmarks, 0600)
  known_hosts         GitHub host keys
```

## Install

```bash
git clone https://github.com/getbible/bookmarks.git /tmp/bookmarks
sudo /tmp/bookmarks/deploy/install.sh
```

The installer is idempotent. It creates the `getbible-bookmarks` system user,
the directories above, the virtualenv from `requirements.txt` (hash verified),
the checkout (cloned anonymously over HTTPS, with the push URL set to the SSH
address so the deploy key is only needed for pushes), `env` from
`deploy/env.template`, an empty registry, the systemd units and timer, copies
the nginx site into `sites-available`, and publishes the first release. Use
`--repo-url` and `--push-url` for a fork or mirror. It then prints the
remaining manual steps:

1. Create a deploy key with write access on GitHub and store it at
   `/etc/getbible-bookmarks/deploy_key`; add GitHub's host keys to
   `known_hosts` (`ssh-keyscan github.com`). Review `/etc/getbible-bookmarks/env`.
2. Enrol contributors (below).
3. `systemctl start getbible-bookmarks-api`.
4. Enable the nginx site, obtain certificates (`certbot --nginx -d
   bookmarks.getbible.net`), `nginx -t`, `systemctl reload nginx`.

## Tokens

Tokens are managed only on the host, as the service user, against the registry
file named in `env`:

```bash
sudo -u getbible-bookmarks /srv/getbible-bookmarks/venv/bin/getbible-bookmarks tokens \
  --file /etc/getbible-bookmarks/contributors.json \
  create --id jaco --name "Brother Jaco" --email jaco@example.org --role contributor
```

`--name` and `--email` become the git author of that contributor's commits.
Other subcommands: `list`, `revoke --id`, `rotate --id`, `set-role --id --role
maintainer`. The service re-reads the registry when the file changes.

## Services

| Unit | Purpose |
|---|---|
| `getbible-bookmarks-api.service` | the management API on `127.0.0.1:8787` |
| `getbible-bookmarks-sync.timer` / `.service` | every 10 minutes: fetch `main`, verify `v1/`, publish the release (picks up merged pull requests) |

Both units read `/etc/getbible-bookmarks/env`, run as the service user with
`ProtectSystem=strict`, and share a `flock` inside `repo/.git` so a timer run
never overlaps a mutation.

Useful commands:

```bash
systemctl status getbible-bookmarks-api
journalctl -u getbible-bookmarks-api -f
curl -s http://127.0.0.1:8787/v1/manage/health
sudo -u getbible-bookmarks /srv/getbible-bookmarks/repo/deploy/deploy.sh   # publish now
```

## nginx

`deploy/nginx/bookmarks.getbible.net.conf` serves `www/current` as the
document root:

- `/v1/` static files: `gzip_static`, `ETag`, `Cache-Control: public,
  max-age=300, stale-while-revalidate=86400`, open CORS, `GET`/`HEAD`/`OPTIONS`
  only, `open_file_cache` for high request rates;
- `/v1/manage/` proxied to the loopback service with `limit_req`, a 1 MiB body
  cap and a 300 second read timeout (a mutation runs git fetch, commit, push);
- everything else `404`.

Because `current` is a symlink switched with `rename(2)`, nginx never sees a
half-written release. `open_file_cache_valid 30s` bounds how long a cached
descriptor to an old release can be reused.

## Rollback

- **Content**: `git revert <commit>` on `main` (or through a pull request), then
  run `deploy/deploy.sh` or wait for the timer. The management service rebases
  onto the reverted history automatically.
- **Release only**: point `current` at a previous release and reload nothing:
  `ln -sfn releases/<previous checksum> /srv/getbible-bookmarks/www/.current.tmp
  && mv -T /srv/getbible-bookmarks/www/.current.tmp
  /srv/getbible-bookmarks/www/current`. The last three releases are kept.
- **Service**: `systemctl restart getbible-bookmarks-api`. The static API is
  unaffected while the service is down.

## Upgrade

```bash
sudo -u getbible-bookmarks git -C /srv/getbible-bookmarks/repo pull --ff-only
sudo /srv/getbible-bookmarks/venv/bin/python -m pip install --require-hashes -r /srv/getbible-bookmarks/repo/requirements.txt
sudo /srv/getbible-bookmarks/venv/bin/python -m pip install --no-deps /srv/getbible-bookmarks/repo
sudo install -m 0644 /srv/getbible-bookmarks/repo/deploy/systemd/*.service /srv/getbible-bookmarks/repo/deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart getbible-bookmarks-api
```

Re-running `deploy/install.sh` performs the same steps.

## Monitoring

- `GET /v1/manage/health` returns `200` while the process is alive.
- `GET /v1/manage/status` (authenticated) exposes `git.push_pending` and
  `git.last_error`; alert when `push_pending` stays true, which means GitHub has
  not received live changes.
- The static side is ordinary nginx: watch the 5xx rate and the age of
  `www/current/v1/index.json` against the repository head.
