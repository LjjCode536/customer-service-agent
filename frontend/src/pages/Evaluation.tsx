import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Input, Progress, Select, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { useEvaluation } from '../api/queries'
import type { EvaluationCase, EvaluationCheck } from '../api/types'
import { EmptyHint, ErrorAlert, QueryEmpty } from '../components/ListState'
import { matchesKeyword, uniqueOptions } from '../utils/format'
import { listPagination } from '../utils/table'

type ResultFilter = 'all' | 'pass' | 'fail'

function checkValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

export default function Evaluation() {
  const evaluation = useEvaluation()
  const report = evaluation.data
  const cases = report?.cases ?? []
  const [result, setResult] = useState<ResultFilter>('all')
  const [category, setCategory] = useState<string>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const categories = useMemo(() => uniqueOptions(cases.map((row) => row.category)), [cases])
  const filtered = useMemo(
    () =>
      cases.filter((row) => {
        if (result === 'pass' && !row.passed) return false
        if (result === 'fail' && row.passed) return false
        if (category && row.category !== category) return false
        return matchesKeyword(keyword, [row.id, row.name, row.category, row.trace_id, row.error])
      }),
    [cases, result, category, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [result, category, keyword])

  const hasFilters = Boolean(keyword || category || result !== 'all')
  const summary = report?.summary
  const failed = (row: EvaluationCase) => row.checks.filter((check) => !check.passed)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Golden Dataset Evaluation
          </Typography.Title>
          <Typography.Text type="secondary">
            每个用例执行完整 Task / Worker / Workflow 链路，使用离线 Provider 与隔离数据库；点击「重新评测」重跑。
          </Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void evaluation.refetch()} loading={evaluation.isFetching}>
          重新评测
        </Button>
      </Space>

      {evaluation.isError ? (
        <ErrorAlert title="评测执行失败" error={evaluation.error} onRetry={() => void evaluation.refetch()} />
      ) : null}

      <Card style={{ marginBottom: 16 }} loading={evaluation.isLoading}>
        {summary ? (
          <Space orientation="vertical" size="small" style={{ width: '100%' }}>
            <Space wrap size={12}>
              <Progress
                type="circle"
                size={72}
                percent={Math.round((summary.pass_rate ?? 0) * 100)}
                status={summary.failed ? 'exception' : 'success'}
              />
              <Space wrap size={8}>
                <Tag color={summary.failed ? 'red' : 'green'}>
                  PASS {summary.passed}/{summary.total}
                </Tag>
                <Tag color="red">FAIL {summary.failed}</Tag>
                <Typography.Text type="secondary">
                  通过率 {((summary.pass_rate ?? 0) * 100).toFixed(2)}%
                </Typography.Text>
              </Space>
            </Space>
            {summary.failed ? (
              <Alert type="warning" showIcon title={`有 ${summary.failed} 个用例失败，展开行查看失败断言。`} />
            ) : null}
          </Space>
        ) : (
          <EmptyHint filtered={false} emptyText="尚无评测结果，点击「重新评测」执行 Golden Dataset。" />
        )}
      </Card>

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索 Case / Category / Trace"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 280 }}
        />
        <Select
          value={result}
          onChange={setResult}
          style={{ width: 130 }}
          options={[
            { value: 'all', label: '全部结果' },
            { value: 'pass', label: '仅 PASS' },
            { value: 'fail', label: '仅 FAIL' },
          ]}
        />
        <Select
          allowClear
          placeholder="Category"
          value={category}
          onChange={setCategory}
          style={{ width: 180 }}
          options={categories.map((value) => ({ value, label: value }))}
        />
        <Button
          onClick={() => {
            setResult('all')
            setCategory(undefined)
            setKeyword('')
          }}
          disabled={!hasFilters}
        >
          重置过滤
        </Button>
        <Typography.Text type="secondary">
          显示 {filtered.length} / {cases.length} 个用例
        </Typography.Text>
      </Space>

      <Table
        rowKey="id"
        loading={evaluation.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={evaluation.isError}
              loading={evaluation.isLoading}
              filtered={hasFilters}
              emptyText="尚无评测结果，点击「重新评测」执行 Golden Dataset。"
              filteredText="没有匹配的评测用例，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize, '个用例')}
        scroll={{ x: 980 }}
        expandable={{
          expandedRowRender: (row: EvaluationCase) => (
            <Space orientation="vertical" size="small" style={{ width: '100%' }}>
              {row.error ? <Alert type="error" showIcon title="用例执行错误" description={row.error} /> : null}
              <Table
                rowKey="field"
                size="small"
                pagination={false}
                dataSource={row.checks}
                locale={{ emptyText: '该用例没有断言明细' }}
                columns={[
                  { title: '断言字段', dataIndex: 'field', width: 180 },
                  {
                    title: '期望',
                    dataIndex: 'expected',
                    render: (value: unknown) => <Typography.Text code>{checkValue(value)}</Typography.Text>,
                  },
                  {
                    title: '实际',
                    dataIndex: 'actual',
                    render: (value: unknown) => <Typography.Text code>{checkValue(value)}</Typography.Text>,
                  },
                  {
                    title: '结果',
                    dataIndex: 'passed',
                    width: 90,
                    render: (value: boolean) => <Tag color={value ? 'green' : 'red'}>{value ? 'PASS' : 'FAIL'}</Tag>,
                  },
                ]}
              />
              {row.trace_id ? (
                <Space wrap>
                  <Link to={`/traces?trace_id=${encodeURIComponent(row.trace_id)}`}>查看 Trace</Link>
                  <Link to={`/bad-cases?trace_id=${encodeURIComponent(row.trace_id)}&create=1`}>登记 Bad Case</Link>
                </Space>
              ) : null}
            </Space>
          ),
        }}
        columns={[
          { title: 'Case', dataIndex: 'name', width: 260 },
          { title: 'Category', dataIndex: 'category', width: 180 },
          {
            title: '结果',
            dataIndex: 'passed',
            width: 100,
            render: (value: boolean) => <Tag color={value ? 'green' : 'red'}>{value ? 'PASS' : 'FAIL'}</Tag>,
          },
          {
            title: '失败断言',
            width: 260,
            render: (_: unknown, row: EvaluationCase) =>
              failed(row).length
                ? failed(row).map((check: EvaluationCheck) => (
                    <Tag color="red" key={check.field}>
                      {check.field}
                    </Tag>
                  ))
                : '-',
          },
          {
            title: 'Trace',
            dataIndex: 'trace_id',
            width: 160,
            render: (value?: string) =>
              value ? (
                <Link to={`/traces?trace_id=${encodeURIComponent(value)}`}>
                  <Typography.Text copyable style={{ fontSize: 12 }}>
                    {value}
                  </Typography.Text>
                </Link>
              ) : (
                '-'
              ),
          },
        ]}
      />
    </>
  )
}
