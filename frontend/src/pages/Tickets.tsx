import { useEffect, useMemo, useState } from 'react'
import { Button, Input, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useTickets } from '../api/queries'
import type { Ticket } from '../api/types'
import { ErrorAlert, QueryEmpty } from '../components/ListState'
import { formatDateTime, matchesKeyword, statusColor, uniqueOptions } from '../utils/format'
import { listPagination } from '../utils/table'

const priorityColor: Record<string, string> = {
  P0: 'magenta',
  P1: 'red',
  P2: 'orange',
  P3: 'blue',
}

export default function Tickets() {
  const tickets = useTickets()
  const [keyword, setKeyword] = useState('')
  const [priority, setPriority] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [category, setCategory] = useState<string>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const rows = tickets.data ?? []
  const priorities = useMemo(() => uniqueOptions(rows.map((row) => row.priority)), [rows])
  const statuses = useMemo(() => uniqueOptions(rows.map((row) => row.status)), [rows])
  const categories = useMemo(() => uniqueOptions(rows.map((row) => row.category)), [rows])

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (priority && row.priority !== priority) return false
        if (status && row.status !== status) return false
        if (category && row.category !== category) return false
        return matchesKeyword(keyword, [
          row.id,
          row.user_id,
          row.order_id,
          row.category,
          row.description,
          row.assigned_group,
        ])
      }),
    [rows, priority, status, category, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [keyword, priority, status, category])

  function resetFilters() {
    setKeyword('')
    setPriority(undefined)
    setStatus(undefined)
    setCategory(undefined)
  }

  const hasFilters = Boolean(keyword || priority || status || category)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Ticket Center
          </Typography.Title>
          <Typography.Text type="secondary">
            工单按创建时间倒序排列，可按优先级、状态、分类与关键字过滤。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            共 {filtered.length} / {rows.length} 条
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void tickets.refetch()} loading={tickets.isFetching}>
            刷新
          </Button>
        </Space>
      </Space>

      {tickets.isError ? (
        <ErrorAlert title="工单列表加载失败" error={tickets.error} onRetry={() => void tickets.refetch()} />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索 Ticket / 用户 / 订单 / 描述"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 280 }}
        />
        <Select
          allowClear
          placeholder="优先级"
          value={priority}
          onChange={setPriority}
          style={{ width: 120 }}
          options={priorities.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="状态"
          value={status}
          onChange={setStatus}
          style={{ width: 140 }}
          options={statuses.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="分类"
          value={category}
          onChange={setCategory}
          style={{ width: 180 }}
          options={categories.map((value) => ({ value, label: value }))}
        />
        <Button onClick={resetFilters} disabled={!hasFilters}>
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={tickets.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={tickets.isError}
              loading={tickets.isLoading}
              filtered={hasFilters}
              emptyText="暂无工单。到 Agent Lab 运行物流异常或投诉 Case 后会生成工单。"
              filteredText="没有匹配的工单，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 1180 }}
        columns={[
          { title: 'Ticket', dataIndex: 'id', width: 180, render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
          {
            title: '优先级',
            dataIndex: 'priority',
            width: 90,
            render: (value?: Ticket['priority']) =>
              value ? <Tag color={priorityColor[value] ?? 'default'}>{value}</Tag> : '-',
          },
          { title: '分类', dataIndex: 'category', width: 160, render: (value?: string) => value || '-' },
          {
            title: '描述',
            dataIndex: 'description',
            width: 260,
            render: (value?: string) =>
              value ? (
                <Tooltip title={value}>
                  <Typography.Text ellipsis style={{ display: 'inline-block', maxWidth: 240 }}>
                    {value}
                  </Typography.Text>
                </Tooltip>
              ) : (
                '-'
              ),
          },
          { title: '用户', dataIndex: 'user_id', width: 100, render: (value?: string) => value || '-' },
          { title: '订单', dataIndex: 'order_id', width: 120, render: (value?: string) => value || '-' },
          { title: '处理组', dataIndex: 'assigned_group', width: 150, render: (value?: string) => value || '-' },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (value?: string) =>
              value ? <Tag color={statusColor(value)}>{value}</Tag> : '-',
          },
          {
            title: 'SLA 截止',
            dataIndex: 'sla_due_at',
            width: 180,
            render: (value?: string | null) => formatDateTime(value),
          },
          {
            title: '创建时间',
            dataIndex: 'created_at',
            width: 180,
            render: (value: string) => formatDateTime(value),
          },
        ]}
      />
    </>
  )
}
