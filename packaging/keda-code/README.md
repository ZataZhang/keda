# keda-code

**你要装的多半不是这个包。** 正式分发名是 [`kedacode`](https://pypi.org/project/kedacode/)：

```bash
uv tool install kedacode   # 或 pipx install kedacode
iar --version
```

`keda-code` 是一个转发包：它本身不含任何代码，只声明对 `kedacode` 的依赖。它存在的唯一理由是占住这个与 `kedacode` 极易混淆的名字，避免被第三方注册成投毒包。按 PEP 503 归一化，`keda-code`、`keda_code`、`keda.code` 是同一个名字，本包一并覆盖。

`pip install keda-code` 可以正常把 `kedacode` 装进来并得到 `iar` 命令。但 `uv tool install keda-code` **不会**产生可执行命令——uv 只暴露主包的 entry point，而本包刻意不声明任何 console script（声明了就会与 `kedacode` 的同名文件打架）。请直接安装 `kedacode`。

项目主页与文档：<https://github.com/ZataZhang/keda>
