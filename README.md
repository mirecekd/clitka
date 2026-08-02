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
resource explorer, CloudWatch Logs, Lambda, ECR, EC2, ECS, API Gateway and Systems
Manager work; the remaining per-service modules are being built milestone by
milestone.


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

```bash
clitka ecs clusters                           # what runs where, Fargate or EC2
clitka ecs services prod --unhealthy          # only the ones not fully up
clitka ecs tasks prod --stopped               # including the ones that just died
clitka ecs get prod abc123def456              # and whether a shell would open
clitka ecs exec prod abc123def456             # a shell inside the container
clitka ecs exec prod abc123 --dry-run         # just print the command
```

ECS is the one service the generic explorer cannot reach: **Cloud Control has no
`AWS::ECS::Task` resource type at all**. In the TUI an ECS cluster therefore folds
out into `Services` and `Tasks` sub-branches of its own - open the cluster leaf and
they appear underneath it, filled by the plugin rather than by Cloud Control. What
is inside them is an ordinary resource, so `F3`, `F9` and `x` all work on a task.
`exec` **describes the task before handing the terminal over** and refuses in a
sentence when it cannot work: the task is still starting, it was never launched with
`--enable-execute-command`
(which cannot be switched on afterwards), or its execute-command agent is not up
because the task role is missing `ssmmessages:*`. All four beat reading
`TargetNotConnectedException` on a bare terminal.

```bash
clitka apigw list                             # REST and HTTP APIs together
clitka apigw list --kind HTTP                 # or just one protocol
clitka apigw routes abc123                    # every method, path and authorizer
clitka apigw routes abc123 --open             # only the ones with no authorizer
clitka apigw stages abc123                    # nothing listed = never deployed
clitka apigw invoke abc123 prod --path /pets  # a real request, through the edge
clitka apigw invoke abc123 prod -X POST -b '{"name":"rex"}'
clitka apigw invoke abc123 prod --sign        # SigV4, for an AWS_IAM route
clitka apigw invoke abc123 prod --dry-run     # print the request, send nothing
```

API Gateway is **two unrelated AWS services behind one console page** - a REST API
lives in `apigateway`, an HTTP or WebSocket one in `apigatewayv2` - and CLITKA
hides that: one listing walks both, and every other command asks whichever half
owns the id. `invoke` is the reason the plugin exists. `aws apigateway
test-invoke-method` **bypasses the entire edge** (no authorizer, no stage variable,
no WAF, REST only), so it answers a different question; this sends a real HTTP
request to the real URL and **exits 1 on any non-2xx**, which is what makes it
usable in a pipeline. It also explains the single most misleading message in AWS:
a 403 saying `Missing Authentication Token` almost never means a missing token - it
is what an *unmatched route* says.

```bash
clitka ssm params                             # every parameter - metadata only
clitka ssm params -c database                 # the bit of the name you remember
clitka ssm get /app/prod/url                  # a SecureString stays hidden
clitka ssm get /app/prod/pw --decrypt         # ...unless you ask, right here
clitka ssm path /app/prod                     # one app's whole config in one call
clitka ssm history /app/prod/url              # the recent versions
clitka ssm put /app/prod/url https://x --overwrite
clitka ssm delete /app/prod/url               # every version goes; asks first
clitka ssm docs                               # your documents, not AWS's hundreds
clitka ssm doc AWS-RunShellScript             # what it is, and what it wants
clitka ssm run AWS-RunShellScript i-0abc -p 'commands=uptime'
```

**A `SecureString` is never decrypted unless that exact command asked to be.**
Without `--decrypt` it reads as `<SecureString, hidden>` - not as its ciphertext,
which is unreadable and yet looks like something worth pasting somewhere. The tree
and the preview pane never decrypt at all, and `F9` offers *the command* rather
than the value: a keystroke is too cheap for putting a production password onto a
screen that may be shared, recorded, or scrolled back through an hour later.
That masking lives in `core/redact.py` and is applied where Cloud Control's
properties enter the app, **not** in the SSM plugin - because `GetResource` on a
parameter volunteers the ciphertext whether anyone asked or not, so `F3` and the
Raw tab would otherwise walk straight around the rule.


`run` exists for the same reason `lambda invoke` does. `aws ssm send-command`
exits 0 as soon as AWS has *accepted* the request - before the script has run at
all - so it cannot tell you anything. This waits, prints stdout and stderr, and
**exits 1 when the script did not succeed**. Every knowable complaint arrives
first, as a sentence: an `Automation` document cannot be sent to an instance, a
required parameter is missing, the document does not run on that platform.
Running a document is deliberately **not** an `F9` action - `SendCommand` executes
a script on someone's machine and there is no undo.




The TUI is split: the tree of resource types on the left, a preview of what you
picked on the right. Nothing is fetched until you open a branch - `enter` (or
`space`) unfolds a type and its resources stream in page by page, `enter` again
folds it and keeps them. `enter` on a *resource* fills the preview pane; moving the
cursor never costs an API call. Some resources hold more: an **ECS cluster** opens
into `Services` and `Tasks`, and a service into its own `Tasks` - the only route to
an ECS task, which has no Cloud Control type. Resources are listed by their **name**
where they have one - the `Name` tag on an EC2 instance, say - with the identifier,
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

`F1`, `P`, `R`, `W` and `C` drop a panel out from under the menu bar - help,
switching the profile or region **for the running session**, the time window, and
the configuration. The letters take either case. Signing in is deliberately **not**
a screen: run `clitka auth login` (or `aws sso login`) in a shell and press `F5` to
pick the new token up. The status bar always names the CLITKA build plus the
profile, account and region a call would use, and says READ-ONLY when writes are
refused.

**`C` is the only screen that writes anything** - `P`, `R` and `W` change the
running session and nothing else, which is deliberate: a keystroke that quietly
edits a config file is a keystroke you cannot trust. `C` is where a session choice
is promoted to a default on purpose, so every row names the value it would save
("save eu-central-1") rather than merely the setting. It offers:

- **the explorer's branches** (`b`) - a checklist of resource types where `space`
  adds or removes one and `/` filters; the candidates are the same live
  `ListTypes` the `:` palette uses. This is what makes the first screen *yours*
  instead of the eleven types CLITKA ships with, and `d` puts those back. The list
  shows the types already in your tree plus a window of the rest, because a real
  account exposes ~1800 of them - **`/` is how you reach a type that is not
  on screen**, and it is faster than scrolling to it would have been.

- **the default profile, region and time window** - the same three things `P`, `R`
  and `W` switch, made to stick. `clitka ctx use` does the first two from a shell.
- **read-only by default**, and **"start where I stopped"** (off by default).

Where those live follows the XDG split rather than one file for everything:
`~/.config/clitka/config.toml` holds what you *chose*, and
`~/.local/state/clitka/state.toml` holds what CLITKA *noticed* - the profile and
region in force when the last session ended. Only the second is written without
being asked, and only when "start where I stopped" is on; it is also the weakest
voice in the room, so it can fill a gap but never beat a `--profile` flag,
`AWS_PROFILE`, or the config file. Both honour `XDG_CONFIG_HOME` / `XDG_STATE_HOME`.


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
| `C` | configuration - **the only screen that saves anything**: which types the explorer opens with, the default profile / region / time window, read-only, and "start where I stopped" |
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
| `enter` / `space` | open a type (loads it) or close it again; on a resource, preview it - and unfold its sub-branches where it has any (an ECS cluster's `Tasks`) |
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

It starts at `1h`, or at whatever `C` saved, and lasts for the running session
only. `clitka logs search --since 3h` is the same window from a shell.

### The configuration panel (`C`)

| Key | What it saves |
|---|---|
| `b` | the explorer's branches - `space` toggles a type, `/` filters, `escape` is done |
| `p` / `r` | this session's profile / region, as the default to start in |
| `w` | this session's time window, as the default to start in |
| `o` | read-only by default |
| `l` | start where the last session stopped |
| `d` | reset the branches to the built-in list |

Everything here is written to `~/.config/clitka/config.toml` and the panel says
what it wrote. Nothing else in the TUI writes anything.


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
`AWS_REGION`, then `~/.config/clitka/config.toml`, then
`~/.local/state/clitka/state.toml` where "start where I stopped" is on, then the
AWS defaults. CLITKA only reads `~/.aws/*`; its own settings go to its own files.
The SSO token is written to `~/.aws/sso/cache` in the exact `aws` CLI v2 layout, so
`clitka auth login` and `aws sso login` are interchangeable.

Roadmap, in order:

1. Auth and context - profiles, IAM Identity Center login, region switching (done)
2. TUI shell and the generic resources explorer (Cloud Control API) (done)
3. CloudWatch Logs, including live tail (done)
4. Lambda, ECS exec and EC2 SSM (the `x` handoff), ECR, EC2 start/stop/reboot,
   API Gateway invoke, Systems Manager - Parameter Store, documents and
   run-command (done)

5. Configuration (`C`) - the explorer's branches, the startup defaults, and
   starting where the last session stopped (done)
6. S3 browser and DynamoDB
7. CloudFormation, SAM and CDK wrappers, Step Functions, EventBridge Schemas
8. Distribution: PyPI, standalone binaries, Docker image, plugin guide


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
| API Gateway | REST and HTTP APIs, routes, stages, **invoke through the real edge**, SigV4 signing |
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
