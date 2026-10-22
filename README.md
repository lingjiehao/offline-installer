# offline-installer

把内网/离线机器需要的开发工具收集到一个 GitHub Release 里，由 GitHub Actions 定期检查上游新版本并自动更新。

- 目标平台：**Linux x64**
- 产物只搬运上游官方二进制，**不做修改、不重新打包**（`fd`、`ripgrep`、`clangd`）
- 使用者无需 root、无需联网：下载 → 解压 → 拷进 `~/.local/bin`

## 当前包含

| 包 | 上游 | 提供的命令 | 说明 |
|---|---|---|---|
| `ripgrep` | [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | `rg` | 静态 musl 构建 |
| `fd` | [sharkdp/fd](https://github.com/sharkdp/fd) | `fd` | 静态 musl 构建 |
| `clangd` | [clangd/clangd](https://github.com/clangd/clangd) | `clangd` | 官方 Linux 包约 112 MB，需要 glibc ≥ 2.18 |

所有产物发布在同一个滚动 Release：

- 列表：`https://github.com/lingjiehao/offline-installer/releases/tag/bundles`
- 清单：`https://github.com/lingjiehao/offline-installer/releases/latest/download/index.json`
- 校验：`https://github.com/lingjiehao/offline-installer/releases/latest/download/SHA256SUMS`

## 使用（离线机器）

联网机器上下载需要的文件，拷贝到内网机器后执行对应的安装命令（`index.json` 里也带了同样的命令）：

```sh
# ripgrep
tar -xzf ripgrep-<version>-x86_64-unknown-linux-musl.tar.gz
install -m755 ripgrep-<version>-x86_64-unknown-linux-musl/rg ~/.local/bin/

# fd
tar -xzf fd-v<version>-x86_64-unknown-linux-musl.tar.gz
install -m755 fd-v<version>-x86_64-unknown-linux-musl/fd ~/.local/bin/

# clangd
unzip clangd-linux-<version>.zip
install -m755 clangd_<version>/bin/clangd ~/.local/bin/
```

确认 `~/.local/bin` 在 `PATH` 中即可。校验下载内容：

```sh
sha256sum -c SHA256SUMS --ignore-missing
```

## 自动更新

`.github/workflows/build.yml`：

- 每周一 04:00 UTC 检查所有包的上游版本
- 上游版本已发布过的包会跳过下载（`plan` 读取 Release 里的 `state.json`）
- 支持手动触发：`workflow_dispatch`，可指定包、可 `force` 强制重建
- 每个包只保留最近 3 个版本（`config.toml` 的 `retention`），旧资产自动删除
- 发布后刷新 `index.json`、`SHA256SUMS`、`state.json` 和 Release 说明

首次使用需要在仓库 **Settings → Actions → General → Workflow permissions** 里选择 **Read and write permissions**，否则 `GITHUB_TOKEN` 无法创建 Release。

## 添加一个包

新建 `packages/<id>/package.toml`：

```toml
id = "yq"
type = "github"
repo = "mikefarah/yq"
description = "YAML 处理器"

asset = "yq_linux_amd64"                       # {version} / {tag} 占位符
bins = ["yq"]
install = "install -m755 {asset} ~/.local/bin/yq"
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 否 | 默认取目录名 |
| `type` | 否 | 初版只支持 `github` |
| `repo` | 是 | `owner/name` |
| `asset` | 是 | 上游资产名，支持 `{version}`、`{tag}` |
| `bins` | 否 | 该包提供的命令，写入 `index.json` |
| `description` | 否 | 写入 Release 说明和 `index.json` |
| `note` | 否 | 额外注意事项（glibc 要求等） |
| `install` | 否 | 安装命令模板，支持 `{asset}`、`{version}` |
| `version_regex` | 否 | tag 无法用"去掉 v 前缀"解析时使用，第一个捕获组为版本 |

提交后 push 到 `main` 会自动触发构建；也可以手动 dispatch 只构建这一个包。

## 本地开发

只依赖 Python 3.11+ 标准库（`tomllib`），无需安装任何第三方包。

```sh
# 单元测试
python -m unittest discover -s tests -v

# 查看哪些包需要构建（会访问 GitHub API；未认证时限 60 次/小时）
python scripts/build.py plan

# 本地构建单个包（下载到 .work/，产物在 dist/）
python scripts/build.py build --pkg fd

# 预览发布结果，不联网
python scripts/publish.py --dry-run --repo lingjiehao/offline-installer
```

注意：

- 工作目录用 `--work-dir` / `OFFLINE_WORK_DIR` 指定；构建完成后默认删除下载的临时文件，`--keep` 可保留。
- `clangd` 解压后体积较大，建议只在 CI 构建；本地调试用 `fd` / `ripgrep` 即可。
- `publish.py` 需要 `GITHUB_TOKEN`（CI 里自动提供）。

## 已知限制

- 仅支持 Linux x64 与 GitHub Release 类型的资产（不处理 pip/npm 等需要依赖解析的包）。
- 上游资产改名会导致 `plan` 报错并列出可用资产名，需要更新对应 `package.toml`。
- `clangd` 需要 glibc ≥ 2.18；老系统（CentOS 7 自带 2.17）无法运行。
