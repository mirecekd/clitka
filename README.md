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

- **TUI** - run `clitka` with no arguments. Keyboard-first, status bar with
  profile / account / region on top, function-key menu bar at the bottom,
  `F9` for the context actions of the selected resource.
- **CLI** - run `clitka <service> <verb>`. Every action available in the TUI is
  also a plain, scriptable command with `--output json|yaml|table`.

It is not another read-only resource browser. The point is the **actions**:
invoke, deploy, tail, exec, upload, execute.

## Status

**Pre-alpha, under active development.** The context layer and the CLI skeleton
work; the TUI and the service modules are being built milestone by milestone.

Roadmap, in order:

1. Auth and context - profiles, IAM Identity Center login, region switching
2. TUI shell and the generic resources explorer (Cloud Control API)
3. CloudWatch Logs, including live tail
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
