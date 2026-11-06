import { useEffect, useMemo, useState } from 'react'
import { Button, Input, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useRefunds } from '../api/queries'
import { ErrorAlert, QueryEmpty } from '../components/ListState'
import { formatAmount, formatDateTime, matchesKeyword, statusColor, uniqueOptions } from '../utils/format'
import { listPagination } from '../utils/table'

export default function Refunds() {
  const refunds = useRefunds()
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const rows = refunds.data ?? []
  const statuses = useMemo(() => uniqueOptions(rows.map((row) => row.status)), [rows])

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (status && row.status !== status) return false
        return matchesKeyword(keyword, [
          row.id,
          row.order_id,
          row.user_id,
          row.external_refund_id,
          row.idempotency_key,
          row.reason,
        ])
      }),
    [rows, status, keyword],
  )
  const totalAmount = useMemo(() => filtered.reduce((sum, row) => sum + (row.amount ?? 0), 0), [filtered])

  useEffect(() => {
    setPage(1)
  }, [keyword, status])

  function resetFilters() {
    setKeyword('')
    setStatus(undefined)
  }

  const hasFilters = Boolean(keyword || status)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Refund Center
          </Typography.Title>
          <Typography.Text type="secondary">
            退款幂等键与外部退款号可检索，当前列表金额合计 {formatAmount(totalAmount)}。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            共 {filtered.length} / {rows.length} 条
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void refunds.refetch()} loading={refunds.isFetching}>
            刷新
          </Button>
        </Space>
      </Space>

      {refunds.isError ? (
        <ErrorAlert title="退款列表加载失败" error={refunds.error} onRetry={() => void refunds.refetch()} />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索退款单 / 订单 / 用户 / 幂等 Key"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 320 }}
        />
        <Select
          allowClear
          placeholder="状态"
          value={status}
          onChange={setStatus}
          style={{ width: 160 }}
          options={statuses.map((value) => ({ value, label: value }))}
        />
        <Button onClick={resetFilters} disabled={!hasFilters}>
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={refunds.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={refunds.isError}
              loading={refunds.isLoading}
              filtered={hasFilters}
              emptyText="暂无退款记录。到 Agent Lab 运行低风险或高额退款 Case 后会生成退款单。"
              filteredText="没有匹配的退款记录，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 1280 }}
        columns={[
          { title: '退款单', dataIndex: 'id', width: 200, render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
          { title: '订单', dataIndex: 'order_id', width: 120, render: (value?: string) => value || '-' },
          { title: '用户', dataIndex: 'user_id', width: 100, render: (value?: string) => value || '-' },
          {
            title: '金额',
            dataIndex: 'amount',
            width: 120,
            align: 'right',
            render: (value?: number | null) => formatAmount(value),
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (value?: string) =>
              value ? <Tag color={statusColor(value)}>{value}</Tag> : '-',
          },
          {
            title: '外部退款号',
            dataIndex: 'external_refund_id',
            width: 200,
            render: (value?: string | null) => value || '-',
          },
          {
            title: '幂等 Key',
            dataIndex: 'idempotency_key',
            width: 260,
            render: (value?: string | null) =>
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
          { title: '原因', dataIndex: 'reason', width: 200, render: (value?: string | null) => value || '-' },
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
