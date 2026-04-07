# Frequently Asked Questions (FAQ)

## General

### How do I restart SeedSync Debian Service?

SeedSync can be restarted from the web GUI. If that fails, you can restart the service from command-line:

    :::bash
    sudo service seedsync restart


### How can I save my settings across updates when using the Docker image?

To maintain state across updates, you can store the settings in the host machine.
Add the following option when starting the container.

    :::bash
    -v <directory on host>:/config

where `<directory on host>` refers to the location on host machine where you wish to store the application
state.


## Security

### Does SeedSync collect any data?

No, SeedSync does not collect any data.


## Troubleshooting

### SeedSync can't seem to connect to my remote server?

Make sure your remote server address was entered correctly.
If using password-based login, make sure the password is correct.
Check the logs for details about the exact failure.

### What is the breadcrumb trace recorder for?

It is an opt-in, low-overhead recorder for the recent lead-up to a failure.
When enabled, it keeps a short bounded history of structured breadcrumbs so you can debug a stuck or surprising workflow without turning normal logs into debug spam.

### How do I use breadcrumb diagnostics?

Turn it on in `Settings > General` with `Enable breadcrumb trace recorder`, reproduce the problem, then read `GET /server/breadcrumbs/get` for the recent breadcrumb window with an authenticated admin session or an admin-scoped API key.
If you are polling after an earlier snapshot, use `since_version=<n>` to ask for only newer entries.

### How are breadcrumbs different from normal logs?

Normal logs are for durable operational history and broad troubleshooting.
Breadcrumbs are for a small recent failure window with high-signal state changes, retries, and transitions.
Keep long-lived, audit-style messages in normal logs and keep breadcrumbs focused on the lead-up to one problem.

### Are breadcrumb entries sensitive?

They are bounded and designed to redact common sensitive values.
The recorder is meant to capture useful context, not full command streams, raw payloads, or exhaustive secret scrubbing.

### I am getting some errors about locale?

On some servers you may see errors in the log like so:
`Unpickling error: unpickling stack underflow b'bash: warning: setlocale: LC_ALL: cannot change locale`

This means your remote server requires that the locale matches with the SeedSync app.
We can fix this by changing the locale for SeedSync.
For SeedSync Docker installs, try adding the following options to the `docker run` command:
```
-e LC_ALL=en_US.UTF-8
-e LANG=en_US.UTF-8
```

See the [issue tracker](https://github.com/truedarkdev/seedsync/issues) for more details.
