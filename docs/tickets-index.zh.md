# 工单索引

这个仓库对于多仓库工单不单独存一份工单文档——本仓做了什么见 `docs/modules/README.zh.md`。设计/自测文档本身落在工单被指定的主仓库里。

| 工单 | 标题 | 状态 | 文档（主仓库） |
|---|---|---|---|
| PRD-20321 | Connector gaps in Graph, CW, EXO（本仓部分：`graph_get_user`/`graph_update_user` 新增 `employeeId`/`givenName`/`surname`，commit `0714c88`） | 冻结 @ 0714c88 | [EN](https://github.com/MSPbotsAI/mcp-service-gateway/blob/dev01.mbagent/PRD-20321/docs/tickets/PRD-20321.md) · [中文](https://github.com/MSPbotsAI/mcp-service-gateway/blob/dev01.mbagent/PRD-20321/docs/tickets/PRD-20321.zh.md) |
| PRD-20413 | Microsoft Graph 新增 `graph_read_site_file_text`，按站点+文件名一次调用读 SharePoint 文本文件（不再经手 driveId），commit `2514f69`——本工单只涉及这一个仓库 | 冻结 @ 2514f69 | [EN](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20413/docs/tickets/PRD-20413.md) · [中文](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20413/docs/tickets/PRD-20413.zh.md) |
| PRD-20393 | Graph offboarding API 变更——第一轮交付了 8 项里的 1–6 项（`722e955`，已作为 `master@4731160` 的一部分发布）；第二轮（`PRD-20393-r2.md`）在 2026-10-14 一次针对真实租户的验收测试发现缺陷后，返工了其中 4 项——本工单只涉及这一个仓库。第一轮当时没有被收进这份索引（它自己「交接」一节引用的还是本文件头部「仅多仓库工单」那条旧规矩）；第二轮依然加了这一行，沿用上面 PRD-20413 的先例——单仓库工单也值得索引——尚未合入 `master`。 | 冻结 @ ccf3931（第二轮；第一轮冻结 @ 722e955） | [EN](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20393/docs/tickets/PRD-20393-r2.md) · [中文](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20393/docs/tickets/PRD-20393-r2.zh.md)（第一轮，已被取代：[EN](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20393/docs/tickets/PRD-20393.md) · [中文](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20393/docs/tickets/PRD-20393.zh.md)） |
