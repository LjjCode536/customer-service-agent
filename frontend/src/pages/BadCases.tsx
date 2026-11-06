import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography, message as toast } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { Link, useSearchParams } from 'react-router-dom'
import { useBadCases, useCreateBadCase, useMarkBadCaseFixed } from '../api/queries'
import type { BadCase } from '../api/types'
import { ErrorAlert, QueryEmpty, errorText } from '../components/ListState'
import { formatDateTime, matchesKeyword, uniqueOptions } from '../utils/format'
import { listPagination } from '../utils/table'

type FixedFilter = 'all' | 'open' | 'fixed'

export default function BadCases() {
  const badCases = useBadCases()
  const createBadCase = useCreateBadCase()
  const markFixed = useMarkBadCaseFixed()
  const [params, setParams] = useSearchParams()
  const traceFilter = params.get('trace_id') ?? ''
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const [keyword, setKeyword] = useState('')
  const [fixedFilter, setFixedFilter] = useState<FixedFilter>('all')
  const [errorType, setErrorType] = useState<string>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const rows = badCases.data ?? []
  const errorTypes = useMemo(() => uniqueOptions(rows.map((row) => row.error_type)), [rows])
  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (traceFilter && row.trace_id !== traceFilter) return false
        if (fixedFilter === 'open' && row.fixed) return false
        if (fixedFilter === 'fixed' && !row.fixed) return false
        if (errorType && row.error_type !== errorType) return false
        return matchesKeyword(keyword, [row.id, row.trace_id, row.query, row.error_type, row.root_cause])
      }),
    [rows, traceFilter, fixedFilter, errorType, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [traceFilter, fixedFilter, errorType, keyword])

  function openCreate() {
    form.resetFields()
    form.setFieldsValue({ trace_id: traceFilter || undefined })
    setOpen(true)
  }

  // Evaluation links here with ?trace_id=…&create=1 to start a traced Bad Case.
  useEffect(() => {
    if (params.get('create') === '1') openCreate()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- open once for the incoming link
  }, [])

  async function create() {
    const values = await form.validateFields()
    let expected_result: Record<string, unknown> = {}
    if (values.expected_result) {
      try {
        expected_result = JSON.parse(values.expected_result)
      } catch {
        toast.error('期望结果必须是合法 JSON')
        return
      }
    }
    try {
      await createBadCase.mutateAsync({
        trace_id: values.trace_id || null,
        query: values.query,
        error_type: values.error_type,
        root_cause: values.root_cause || '',
        expected_result,
      })
      toast.success('Bad Case 已创建')
      setOpen(false)
      form.resetFields()
    } catch (error) {
      toast.error(errorText(error))
    }
  }

  async function fix(id: number) {
    try {
      await markFixed.mutateAsync(id)
      toast.success('已标记修复')
    } catch (error) {
      toast.error(errorText(error))
    }
  }

  const hasFilters = Boolean(traceFilter || keyword || errorType || fixedFilter !== 'all')

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Bad Case Center
          </Typography.Title>
          <Typography.Text type="secondary">
            从评测或 Trace 定位失败样本，回填根因与期望结果后标记修复。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            共 {filtered.length} / {rows.length} 条
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void badCases.refetch()} loading={badCases.isFetching}>
            刷新
          </Button>
          <Button type="primary" onClick={openCreate}>
            登记 Bad Case
          </Button>
        </Space>
      </Space>

      {badCases.isError ? (
        <ErrorAlert title="Bad Case 加载失败" error={badCases.error} onRetry={() => void badCases.refetch()} />
      ) : null}

      {traceFilter ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          title={`仅显示 Trace ${traceFilter} 的 Bad Case`}
          action={
            <Button size="small" onClick={() => setParams({})}>
              清除 Trace 过滤
            </Button>
          }
        />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索 Query / 根因 / Trace"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 300 }}
        />
        <Select
          value={fixedFilter}
          onChange={setFixedFilter}
          style={{ width: 140 }}
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'open', label: '仅未修复' },
            { value: 'fixed', label: '仅已修复' },
          ]}
        />
        <Select
          allowClear
          placeholder="错误类型"
          value={errorType}
          onChange={setErrorType}
          style={{ width: 220 }}
          options={errorTypes.map((value) => ({ value, label: value }))}
        />
        <Button
          onClick={() => {
            setKeyword('')
            setFixedFilter('all')
            setErrorType(undefined)
            setParams({})
          }}
          disabled={!hasFilters}
        >
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={badCases.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={badCases.isError}
              loading={badCases.isLoading}
              filtered={hasFilters}
              emptyText="暂无 Bad Case。评测失败时可在「Evaluation」页一键登记。"
              filteredText="没有匹配的 Bad Case，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 1180 }}
        expandable={{
          expandedRowRender: (row: BadCase) => (
            <Space orientation="vertical" size="small" style={{ width: '100%' }}>
              <Typography.Text>
                <Typography.Text strong>根因：</Typography.Text>
                {row.root_cause || '-'}
              </Typography.Text>
              <Typography.Text strong>期望结果</Typography.Text>
              <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                {JSON.stringify(row.expected_result ?? {}, null, 2)}
              </pre>
            </Space>
          ),
        }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          {
            title: 'Trace',
            dataIndex: 'trace_id',
            width: 190,
            render: (value?: string | null) =>
              value ? (
                <Link to={`/traces?trace_id=${encodeURIComponent(value)}`}>
                  <Typography.Text style={{ fontSize: 12 }}>{value}</Typography.Text>
                </Link>
              ) : (
                '-'
              ),
          },
          { title: 'Query', dataIndex: 'query', width: 300, ellipsis: true },
          { title: '错误类型', dataIndex: 'error_type', width: 200 },
          { title: '根因', dataIndex: 'root_cause', width: 220, ellipsis: true, render: (value: string) => value || '-' },
          {
            title: '状态',
            dataIndex: 'fixed',
            width: 100,
            render: (value: boolean) => (value ? <Tag color="green">fixed</Tag> : <Tag color="orange">open</Tag>),
          },
          {
            title: '创建时间',
            dataIndex: 'created_at',
            width: 180,
            render: (value: string) => formatDateTime(value),
          },
          {
            title: '操作',
            width: 130,
            fixed: 'right',
            render: (_: unknown, row: BadCase) =>
              row.fixed ? (
                '-'
              ) : (
                <Button size="small" loading={markFixed.isPending} onClick={() => void fix(row.id)}>
                  标记已修复
                </Button>
              ),
          },
        ]}
      />

      <Modal
        title="登记 Bad Case"
        open={open}
        onOk={() => void create()}
        confirmLoading={createBadCase.isPending}
        onCancel={() => setOpen(false)}
        okText="创建"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="trace_id" label="Trace ID">
            <Input placeholder="trace_xxx" />
          </Form.Item>
          <Form.Item name="query" label="失败 Query" rules={[{ required: true, message: '请输入 Query' }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="error_type" label="错误类型" rules={[{ required: true, message: '请输入错误类型' }]}>
            <Input placeholder="wrong_approval_chain" />
          </Form.Item>
          <Form.Item name="root_cause" label="根因">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="expected_result" label="期望结果（JSON）">
            <Input.TextArea rows={3} placeholder='{"approval_chain":["refund_specialist"]}' />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
