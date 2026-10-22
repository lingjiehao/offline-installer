# offline-installer

把内网/离线机器需要的开发工具收集到一个 GitHub Release 里，由 GitHub Actions 定期检查上游新版本并自动更新。

- 目标平台：**Linux x64**
- `github` 类型：原样搬运上游官方二进制，不做修改
- `wheelhouse` 类型：下载 pip 依赖的全部 wheel、锁定版本，附带离线 `install.sh`

## 当前包含

| 包 | 来源 | 类型 | 说明 |
|---|---|---|---|
| `ripgrep` | [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | github | 提供 `rg`，静态 musl 构建 |
| `fd` | [sharkdp/fd](https://github.com/sharkdp/fd) | github | 提供 `fd`，静态 musl 构建 |
| `clangd` | [clangd/clangd](https://github.com/clangd/clangd) | github | 提供 `clangd`，需要 glibc ≥ 2.18 |
| `compiledb` | [PyPI](https://pypi.org/project/compiledb/) | wheelhouse | 生成 `compile_commands.json`；纯 Python，附 `install.sh` |

所有产物发布在同一个滚动 Release：

- https://github.com/lingjiehao/offline-installer/releases/tag/bundles
- `.../releases/latest/download/index.json` — 每个包的最新版本、大小、sha256
- `.../releases/latest/download/SHA256SUMS` — 校验清单

```sh
sha256sum -c SHA256SUMS --ignore-missing
```

`wheelhouse` 类型的压缩包解压后内有 `install.sh`，默认装到当前 venv/conda 环境，否则 `pip install --user`；也可 `--venv DIR`。

## 自动更新

`.github/workflows/build.yml`：

- 每周一 04:00 UTC 检查上游版本
- `github` 类型：版本已发布过则跳过下载（依据 Release 里的 `state.json`）
- `wheelhouse` 类型：每次重建，依赖内容变化才产生新资产（内容哈希参与文件名）
- 支持手动触发：`workflow_dispatch`，可指定包、可 `force`
- 每个包保留最近 3 个版本（`config.toml` 的 `retention`），旧资产自动删除
- 发布后刷新 `index.json`、`SHA256SUMS`、`state.json` 和 Release 说明

首次使用需要在仓库 **Settings → Actions → General → Workflow permissions** 选择 **Read and write permissions**。

## 添加一个包

新建 `packages/<id>/package.toml`。

`github` 类型（上游 Release 里的现成资产）：

```toml
id = "yq"
type = "github"
repo = "mikefarah/yq"
description = "YAML 处理器"

asset = "yq_linux_amd64"     # 支持 {version} / {tag}
bins = ["yq"]
```

`wheelhouse` 类型（pip 包）：

```toml
id = "compiledb"
type = "wheelhouse"
description = "生成 compile_commands.json"
requirements = ["compiledb"]  # 会下载全部传递依赖
bins = ["compiledb"]
install = "tar -xzf {asset} && ./{dir}/install.sh"
```

`wheelhouse` 包目录里需要提供 `install.sh`（构建时原样打进压缩包）。

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 否 | 默认取目录名 |
| `type` | 否 | `github`（默认）或 `wheelhouse` |
| `repo` / `asset` | github 必填 | `owner/name`；资产名，支持 `{version}`、`{tag}` |
| `requirements` | wheelhouse 必填 | PyPI 包名列表 |
| `bins` | 否 | 提供的命令，写入 `index.json` |
| `description` / `note` | 否 | 写入 Release 说明和 `index.json` |
| `install` | 否 | 命令模板，支持 `{asset}`、`{version}`、`{dir}`；wheelhouse 用 |
| `version_regex` | 否 | tag 无法用"去掉 v 前缀"解析时的正则，第一个捕获组为版本 |

## 本地开发

只依赖 Python 3.11+ 标准库，无需第三方包。

```sh
# 单元测试
python -m unittest discover -s tests -v

# 哪些包需要构建（访问 GitHub API；未认证时限 60 次/小时）
python scripts/build.py plan

# 构建单个包
python scripts/build.py build --pkg fd
python scripts/build.py build --pkg compiledb

# 预览发布结果（不联网）
python scripts/publish.py --dry-run --repo lingjiehao/offline-installer
```

- 工作目录可用 `--work-dir` / `OFFLINE_WORK_DIR` 指定，构建完成后默认清理，`--keep` 保留。
- `clangd` 体积大，建议只在 CI 构建。
- `publish.py` 需要 `GITHUB_TOKEN`（CI 自动提供）。

## 已知限制

- 仅 Linux x64。
- `github` 类型要求上游资产名可预测；上游改名时 `plan` 会报错并列出可用资产。
- `wheelhouse` 目前只支持纯 Python wheel；含 C 扩展的包需要容器内构建，尚未实现。
- `clangd` 需要 glibc ≥ 2.18（CentOS 7 自带 2.17，无法运行）。
