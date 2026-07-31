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
  Keyboard-first, with a function-key menu bar on top, the profile / account /
  region status bar at the bottom, and `F9` for the actions of what is selected.

- **CLI** - run `clitka <service> <verb>`. Every action available in the TUI is
  also a plain, scriptable command with `--output json|yaml|table`.

It is not another read-only resource browser. The point is the **actions**:
invoke, deploy, tail, exec, upload, execute.

## Status

**Pre-alpha, under active development.** Auth, context, the TUI shell, the generic
resource explorer and CloudWatch Logs work; the remaining per-service modules are
being built milestone by milestone.

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
clitka logs search /aws/lambda/my-fn -f ERROR -m 30
clitka logs tail /aws/lambda/my-fn            # live tail, ctrl-c stops it
```

The TUI is split: the tree of resource types on the left, a preview of what you
picked on the right. Nothing is fetched until you open a branch - `enter` (or
`space`) unfolds a type and its resources stream in page by page, `enter` again
folds it and keeps them. `enter` on a *resource* fills the preview pane; moving the
cursor never costs an API call. `:` adds any other type the account exposes as a
further branch, `tab` moves between the tree and the preview, `F9` opens the
actions for what is selected, `F5` forgets everything, `F10` quits.

The preview has an Overview of the grouped properties and a Raw tab with the API
response, and a service can add tabs of its own - a log group, for instance, gets
an Events tab with its last hour. Pressing `t` on a log group opens the **live
tail**: events as they happen, `space` pauses, `w` wraps, `s` saves what is
buffered to a file, `escape` stops the session.

`F1` to `F4` drop a panel out from under the menu bar - help, switching the profile
or region **for the running session**, and signing in. `F4` runs the IAM Identity
Center device flow right there, so an expired login never means dropping to a
shell; `clitka ctx use` is what makes a profile choice stick. The status bar always
names the CLITKA build plus the profile, account and region a call would use, and
says READ-ONLY when writes are refused.

Configuration precedence is `--profile/--region` flag, then `AWS_PROFILE` /
`AWS_REGION`, then `~/.config/clitka/config.toml`, then the AWS defaults.
CLITKA only reads `~/.aws/*`; its own settings go to its own file. The SSO token
is written to `~/.aws/sso/cache` in the exact `aws` CLI v2 layout, so
`clitka auth login` and `aws sso login` are interchangeable.

Roadmap, in order:

1. Auth and context - profiles, IAM Identity Center login, region switching (done)
2. TUI shell and the generic resources explorer (Cloud Control API) (done)
3. CloudWatch Logs, including live tail (done)

4. Lambda, ECS exec, EC2 SSM, ECR, API Gateway invoke, Systems Manager
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
| EC2 | instances, **SSM session**, port forwarding, start/stop |
| ECR | repositories, images, tags |
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
