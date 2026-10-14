# 工单索引

这个仓库对于多仓库工单不单独存一份工单文档——本仓做了什么见 `docs/modules/README.zh.md`。设计/自测文档本身落在工单被指定的主仓库里。

| 工单 | 标题 | 状态 | 文档（主仓库） |
|---|---|---|---|
| PRD-20321 | Connector gaps in Graph, CW, EXO（本仓部分：`graph_get_user`/`graph_update_user` 新增 `employeeId`/`givenName`/`surname`，commit `0714c88`） | 冻结 @ 0714c88 | [EN](https://github.com/MSPbotsAI/mcp-service-gateway/blob/dev01.mbagent/PRD-20321/docs/tickets/PRD-20321.md) · [中文](https://github.com/MSPbotsAI/mcp-service-gateway/blob/dev01.mbagent/PRD-20321/docs/tickets/PRD-20321.zh.md) |
| PRD-20413 | Microsoft Graph 新增 `graph_read_site_file_text`，按站点+文件名一次调用读 SharePoint 文本文件（不再经手 driveId），commit `2514f69`——本工单只涉及这一个仓库 | 冻结 @ 2514f69 | [EN](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20413/docs/tickets/PRD-20413.md) · [中文](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20413/docs/tickets/PRD-20413.zh.md) |
| PRD-20422 | Microsoft Graph 为离职流程 08/03 新增 `graph_transfer_onedrive` 和 `graph_start_cloud_sync`（延期自 PRD-20393 item 7-8），commit `eb38295`——本工单只涉及这一个仓库 | 冻结 @ eb38295 | [EN](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20422/docs/tickets/PRD-20422.md) · [中文](https://github.com/MSPbotsAI/ms-graph-mcp/blob/dev01.mbagent/PRD-20422/docs/tickets/PRD-20422.zh.md) |
