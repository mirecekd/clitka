# CLITKA

**CLI ToolKit for AWS** - the AWS Toolkit workflow, in your terminal.

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg) [!["PayPal.me"](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=LJ5ZF7Q9KMTRW&no_recurring=0&currency_code=USD)

</div>

## What it is

The AWS Toolkit for Visual Studio Code is excellent - and locked inside an
editor GUI. The terminal alternative is a zoo of single-purpose tools, each with
its own flags, config and auth story.

CLITKA is one tool with two faces:

- **TUI** - run `clitka` with no arguments and you land on a tree of resource
  types: open one and its resources unfold underneath, loaded on demand.
  Keyboard-first, with a key menu bar on top, the profile / account / region
  status bar at the bottom, and `F9` for the actions of what is selected.

- **CLI** - run `clitka <service> <verb>`. Every action available in the TUI is
  also a plain, scriptable command with `--output json|yaml|table`.

It is not another read-only resource browser. The point is the **actions**:
invoke, deploy, tail, exec, upload, execute.

## Status

**Pre-alpha, under active development.** Auth, context, the TUI shell, the generic
resource explorer, CloudWatch Logs, Lambda, ECR and EC2 work; the remaining
per-service modules are being built milestone by milestone.


What works today:

```bash
clitka                               # TUI: the resource tree, F1 help, F10 quit
clitka ctx show                      # profile, region, account, identity
clitka ctx profiles                  # profiles with their sso-session and role
clitka ctx use myprofile             # remember a profile (and its region)
clitka auth status                   # per sso-session token expiry
clitka auth login -p myprofile       # IAM Identity Center device flow
clitka auth logout --all
clitka resources types                        # every CFN resource type here
clitka resources list AWS::S3::Bucket         # any type, via Cloud Control API
clitka resources get AWS::S3::Bucket my-bkt
clitka resources delete AWS::S3::Bucket my-bkt
clitka logs groups                            # log groups, size and retention
clitka logs streams /aws/lambda/my-fn
clitka logs search /aws/lambda/my-fn -f ERROR --since 3h
clitka logs tail /aws/lambda/my-fn            # live tail, ctrl-c stops it

clitka lambda list                            # every function in the region
clitka lambda get my-fn                       # config, env vars, log group
clitka lambda invoke my-fn -d '{"a": 1}'      # payload on stdout, logs on stderr
clitka lambda invoke my-fn -D event.json      # ...or read the event from a file
```

`lambda invoke` **exits non-zero when the handler raised**, even though AWS
answers HTTP 200 for that - which is what makes it usable in a script.

```bash
clitka ecr repos                              # every repository in the region
clitka ecr images my-app                      # newest push first, with scan verdict
clitka ecr images my-app --untagged --digests # exactly what a cleanup removes
clitka ecr delete my-app --untagged           # asks first; --yes skips that
clitka ecr login my-app                       # the `docker login` one-liner
```

`ecr delete` always goes **by digest**, never by tag: deleting a tag would remove
the image every other tag also points at, which is how people lose `latest` and
`v3` in one keystroke.

```bash
clitka ec2 list                               # by Name tag, with state and IPs
clitka ec2 list --state stopped               # only the ones costing nothing
clitka ec2 get i-0abc1234                     # type, IPs, VPC, subnet, key, launch
clitka ec2 stop i-0abc1234                    # asks first; --yes skips that
clitka ec2 start i-0abc1234                   # and `reboot`
```

Every power command **reads the state first** and refuses in a sentence: starting
an instance that is already running is a silent no-op at the API, and stopping one
that is still `pending` is an error code. There is deliberately **no `terminate`** -
it cannot be undone, so it stays a console job.


The TUI is split: the tree of resource types on the left, a preview of what you
picked on the right. Nothing is fetched until you open a branch - `enter` (or
`space`) unfolds a type and its resources stream in page by page, `enter` again
folds it and keeps them. `enter` on a *resource* fills the preview pane; moving the
cursor never costs an API call. Resources are listed by their **name** where they
have one - the `Name` tag on an EC2 instance, say - with the identifier beside it,
because `i-0abc1234...` tells nobody which machine that is. `:` adds any other type
the account exposes as a further branch, `tab` moves between the tree and the
preview (the focused side is outlined), `F3` views the selected resource in full,
`F4` is the edit slot, `F9` opens the actions for it, `F5` forgets everything,
`F10` quits.

The preview has an Overview of the grouped properties and a Raw tab with the API
response, and a service can add tabs of its own - a log group, for instance, gets
an Events tab, and an ECR repository gets its Images. How far back a time-based tab
looks is up to you: `W` drops a **time window** picker with presets from 5 minutes
to a year, or a duration you type. Inside the preview the arrows do what they look
like they should: `left`/`right` walk the tabs, `up`/`down` scroll one. Pressing `t`
on a log group opens the **live tail**: events as they happen, `space` pauses, `w`
wraps, `s` saves what is buffered to a file, `escape` stops the session.

`F1`, `P`, `R` and `W` drop a panel out from under the menu bar - help, switching
the profile or region **for the running session**, and the time window. The letters
take either case; `clitka ctx use` is what makes a profile choice stick. Signing in
is deliberately **not** a screen: run `clitka auth login` (or `aws sso login`) in a
shell and press `F5` to pick the new token up. The status bar always names the
CLITKA build plus the profile, account and region a call would use, and says
READ-ONLY when writes are refused.

## Keyboard

Everything, in one place. Letter keys take either case.

### Always available

| Key | What it does |
|---|---|
| `F1` | help for the current screen (`F1` or `escape` closes it) |
| `:` | command palette - open any resource type |
| `P` | switch profile - this session only |
| `R` | switch region - this session only |
| `W` | time window - how far back the log preview and `F9` look |
| `F3` | view the selected resource in full (`GetResource`), as YAML |
| `F4` | edit the selected resource |
| `x` | open a shell on it - an EC2 instance (SSM) or an ECS task (`ecs execute-command`). CLITKA steps aside for the session and comes back when you exit |
| `F5` | refresh |
| `F9` | actions for the selected resource |
| `F10` / `q` | quit |

### The resource tree (the landing screen)

| Key | What it does |
|---|---|
| `up` / `down` | move one node; `page up` / `page down` a screenful |
| `ctrl+home` / `ctrl+end` | first / last node |
| `enter` / `space` | open a type (loads it) or close it again; on a resource, preview it |
| `right` / `left` | open / close without moving off the node |
| `tab` | move between the tree and the preview - the focused side is outlined |
| `t` | on a log group: follow it live (CloudWatch live tail) |
| `F5` | collapse everything and forget it - also the retry after an error |

### Inside the preview pane

| Key | What it does |
|---|---|
| `left` / `right` | walk the tabs (Overview, Raw, Events, ...) |
| `up` / `down` | scroll the tab; `page up` / `page down` page it |
| `home` / `end` | jump to the ends |
| `tab` | back to the tree |

### The time window picker (`W`)

| Key | Window |
|---|---|
| `1` `2` `3` `4` `5` `6` | 5m, 15m, 1h, 3h, 6h, 12h |
| `7` `8` `9` `0` | 24h, 3d, 7d, 2w |
| `n` / `y` | 1 month / 1 year |
| `c` | custom - type a duration: `90m`, `2h`, `3d`, `2w`, `1mo`, `1y` (a bare number means minutes) |

It starts at `1h` and lasts for the running session only.
`clitka logs search --since 3h` is the same window from a shell.

### The flat explorer (what `:` opens outside the tree)

| Key | What it does |
|---|---|
| `/` | filter every row loaded so far (`escape` clears it) |
| `s` | sort by the current column (again reverses it) |
| `escape` | back |

### The live tail screen (`t` on a log group)

| Key | What it does |
|---|---|
| `space` | pause the scroll - events keep arriving and the status says how many |
| `w` | wrap long lines |
| `s` | save the buffer to a file |
| `escape` | stop the session and go back |

Configuration precedence is `--profile/--region` flag, then `AWS_PROFILE` /
`AWS_REGION`, then `~/.config/clitka/config.toml`, then the AWS defaults.
CLITKA only reads `~/.aws/*`; its own settings go to its own file. The SSO token
is written to `~/.aws/sso/cache` in the exact `aws` CLI v2 layout, so
`clitka auth login` and `aws sso login` are interchangeable.

Roadmap, in order:

1. Auth and context - profiles, IAM Identity Center login, region switching (done)
2. TUI shell and the generic resources explorer (Cloud Control API) (done)
3. CloudWatch Logs, including live tail (done)
4. Lambda (done), ECS exec and EC2 SSM (the `x` handoff, done), ECR (done),
   EC2 start/stop/reboot (done), API Gateway invoke, Systems Manager
5. S3 browser and DynamoDB
6. CloudFormation, SAM and CDK wrappers, Step Functions, EventBridge Schemas
7. Distribution: PyPI, standalone binaries, Docker image, plugin guide

## Planned coverage

| Area | What CLITKA does |
|---|---|
| Auth | IAM profiles, IAM Identity Center (SSO) login, account and region switching, token cache shared with `aws` CLI v2 |
| Resources | generic explorer over any CloudFormation resource type via the Cloud Control API |
| CloudWatch Logs | browse groups and streams, search, **live tail** |
| Lambda | list, invoke, download/upload code, env vars, local invoke via `sam` |
| ECS | clusters, services, tasks, **exec into a container** |
| EC2 | instances, **SSM session**, port forwarding, start / stop / reboot |
| ECR | repositories, images and tags, the untagged-image cleanup, `docker login` |
| S3 | browse, upload/download with progress, edit an object in `$EDITOR` |
| DynamoDB | tables, query/scan, item view and edit, PartiQL - **CLITKA's own addition**, the VS Code toolkit barely has this |
| API Gateway | list APIs, resources, methods, **invoke** |
| CloudFormation | stacks, events with the failure reason highlighted, resources, template, drift |
| Step Functions | state machines, start execution, execution history |
| EventBridge Schemas | registries, schemas, code bindings |
| Systems Manager | documents, Parameter Store |
| SAM / CDK | wrappers around the official CLIs - never reimplemented |

## Design principles

- Interoperates with the AWS ecosystem instead of replacing it: reads
  `~/.aws/config`, shares `~/.aws/sso/cache`, reuses `session-manager-plugin`,
  shells out to `sam` and `cdk`.
- Never blocks the UI - all AWS calls run in workers.
- Never swallows an AWS error.
- Destructive actions always confirm and name the target.
- No telemetry. Ever.

## Requirements

- Python 3.11+
- Optional: `session-manager-plugin` (EC2/ECS exec), `sam`, `cdk`

## Install

Not published yet. For development:

```bash
git clone https://github.com/mirecekd/clitka.git
cd clitka
uv sync
uv run clitka --help
```

## Support

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg) [!["PayPal.me"](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=LJ5ZF7Q9KMTRW&no_recurring=0&currency_code=USD)

</div>

## License

MIT - see [LICENSE](LICENSE). (c) 2026 Miroslav Dvorak
