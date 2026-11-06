import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography, message as toast } from 'antd'
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons'
import { useApprovals, useDecideApproval } from '../api/queries'
import type { Approval } from '../api/types'
import { ErrorAlert, QueryEmpty, errorText } from '../components/ListState'
import { formatDateTime, matchesKeyword, statusColor, uniqueOptions } from '../utils/format'
import { listPagination } from '../utils/table'

interface PendingDecision {
  row: Approval
  status: 'approved' | 'rejected'
}

export default function Approvals() {
  const approvals = useApprovals()
  const decide = useDecideApproval()
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<string>('pending')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [decision, setDecision] = useState<PendingDecision>()
  const [comment, setComment] = useState('')

  const rows = approvals.data ?? []
  const statuses = useMemo(
    () => uniqueOptions(['pending', 'approved', 'rejected', 'cancelled', ...rows.map((row) => row.status)]),
    [rows],
  )

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (status && row.status !== status) return false
        return matchesKeyword(keyword, [row.id, row.task_id, row.order_id, row.approver_role, row.approver_user_id, row.comment])
      }),
    [rows, status, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [keyword, status])

  function hasPriorPending(row: Approval) {
    return rows.some(
      (other) =>
        other.task_id === row.task_id &&
        other.id !== row.id &&
        other.status === 'pending' &&
        (other.sequence_no ?? 0) < (row.sequence_no ?? 0),
    )
  }

  async function confirmDecision() {
    if (!decision) return
    try {
      await decide.mutateAsync({ id: decision.row.id, status: decision.status, comment: comment.trim() || undefined })
      toast.success(decision.status === 'approved' ? '审批已通过，任务将被 Worker 恢复' : '审批已驳回')
      setDecision(undefined)
      setComment('')
      // Invalidate covers status changes; refetch makes the table update immediately.
      void approvals.refetch()
    } catch (error) {
      toast.error(errorText(error))
    }
  }

  const hasFilters = Boolean(keyword || status !== 'pending')
  const pendingCount = rows.filter((row) => row.status === 'pending').length

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Human Review
          </Typography.Title>
          <Typography.Text type="secondary">
            待审批 {pendingCount} 条；决策后任务进入 resume_pending，由 Worker 继续执行，列表每 5 秒自动刷新。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            当前显示 {filtered.length} / {rows.length} 条
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void approvals.refetch()} loading={approvals.isFetching}>
            刷新
          </Button>
        </Space>
      </Space>

      {approvals.isError ? (
        <ErrorAlert title="审批列表加载失败" error={approvals.error} onRetry={() => void approvals.refetch()} />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索审批 / 任务 / 订单 / 角色"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 300 }}
        />
        <Select
          placeholder="状态"
          value={status}
          onChange={setStatus}
          style={{ width: 150 }}
          options={statuses.map((value) => ({ value, label: value }))}
        />
        <Button onClick={() => setStatus('pending')} disabled={status === 'pending'}>
          只看待审批
        </Button>
        <Button
          onClick={() => {
            setKeyword('')
            setStatus('pending')
          }}
          disabled={!hasFilters}
        >
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={approvals.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={approvals.isError}
              loading={approvals.isLoading}
              filtered={hasFilters}
              emptyText={
                status === 'pending'
                  ? '当前没有待审批记录，切换状态可查看历史决策。'
                  : '暂无审批记录。到 Agent Lab 运行高额退款 Case 后会生成两级人工审核。'
              }
              filteredText="没有匹配的审批，试试切换状态或清空过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 1220 }}
        columns={[
          { title: '审批', dataIndex: 'id', width: 190, render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
          { title: '任务', dataIndex: 'task_id', width: 190, render: (value?: string) => value || '-' },
          { title: '订单', dataIndex: 'order_id', width: 120, render: (value?: string) => value || '-' },
          { title: '角色', dataIndex: 'approver_role', width: 160, render: (value?: string) => value || '-' },
          { title: '顺序', dataIndex: 'sequence_no', width: 80, render: (value?: number) => value ?? '-' },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (value?: string) =>
              value ? <Tag color={statusColor(value)}>{value}</Tag> : '-',
          },
          { title: '审批人', dataIndex: 'approver_user_id', width: 120, render: (value?: string | null) => value || '-' },
          { title: '备注', dataIndex: 'comment', width: 180, render: (value?: string | null) => value || '-' },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            width: 180,
            render: (value: string) => formatDateTime(value),
          },
          {
            title: '操作',
            width: 190,
            fixed: 'right',
            render: (_: unknown, row: Approval) => {
              const blocked = hasPriorPending(row)
              const disabled = row.status !== 'pending' || decide.isPending || blocked
              return (
                <Space>
                  <Tooltip title={blocked ? '请先完成前置审批' : undefined}>
                    <Button
                      size="small"
                      type="primary"
                      icon={<CheckOutlined />}
                      disabled={disabled}
                      onClick={() => {
                        setComment('')
                        setDecision({ row, status: 'approved' })
                      }}
                    >
                      通过
                    </Button>
                  </Tooltip>
                  <Tooltip title={blocked ? '请先完成前置审批' : undefined}>
                    <Button
                      size="small"
                      danger
                      icon={<CloseOutlined />}
                      disabled={disabled}
                      onClick={() => {
                        setComment('')
                        setDecision({ row, status: 'rejected' })
                      }}
                    >
                      驳回
                    </Button>
                  </Tooltip>
                </Space>
              )
            },
          },
        ]}
      />

      <Modal
        open={Boolean(decision)}
        title={decision?.status === 'approved' ? '通过审批' : '驳回审批'}
        okText={decision?.status === 'approved' ? '确认通过' : '确认驳回'}
        okButtonProps={{ danger: decision?.status === 'rejected', loading: decide.isPending }}
        onOk={() => void confirmDecision()}
        onCancel={() => {
          setDecision(undefined)
          setComment('')
        }}
      >
        {decision ? (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Alert
              type={decision.status === 'approved' ? 'info' : 'warning'}
              showIcon
              title={
                decision.status === 'approved'
                  ? '通过后，若该任务的全部审批均已完成，任务将被 Worker 恢复执行。'
                  : '驳回后，同一任务的其余待审批记录会被取消。'
              }
            />
            <Typography.Text>
              {decision.row.approver_role} · 顺序 {decision.row.sequence_no ?? '-'} · 任务 {decision.row.task_id ?? '-'}
            </Typography.Text>
            <Input.TextArea
              rows={3}
              placeholder="审批备注（可选）"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
          </Space>
        ) : null}
      </Modal>
    </>
  )
}
