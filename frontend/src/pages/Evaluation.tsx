import { Button, Card, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useEvaluation } from '../api/queries'
import type { EvaluationCase } from '../api/types'

export default function Evaluation() {
  const evaluation = useEvaluation()
  const report = evaluation.data
  const failed = (row: EvaluationCase) => row.checks.filter((check) => !check.passed)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Golden Dataset Evaluation
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => void evaluation.refetch()} loading={evaluation.isFetching}>
          重新评测
        </Button>
      </Space>
      <Card style={{ marginBottom: 16 }} loading={evaluation.isLoading}>
        <Space wrap>
          {report?.summary ? (
            <Tag color={report.summary.failed ? 'red' : 'green'}>
              PASS {report.summary.passed}/{report.summary.total}
            </Tag>
          ) : null}
          <Typography.Text type="secondary">
            每个用例执行完整 Task / Worker / Workflow 链路，使用离线 Provider 与隔离数据库。
          </Typography.Text>
        </Space>
      </Card>
      <Table
        rowKey="id"
        loading={evaluation.isLoading}
        dataSource={report?.cases ?? []}
        pagination={false}
        expandable={{
          expandedRowRender: (row: EvaluationCase) => (
            <pre style={{ whiteSpace: 'pre-wrap' }}>
              {JSON.stringify({ expected: row.expected, actual: row.actual, error: row.error }, null, 2)}
            </pre>
          ),
        }}
        columns={[
          { title: 'Case', dataIndex: 'name' },
          { title: 'Category', dataIndex: 'category' },
          {
            title: '结果',
            dataIndex: 'passed',
            render: (value: boolean) => <Tag color={value ? 'green' : 'red'}>{value ? 'PASS' : 'FAIL'}</Tag>,
          },
          {
            title: '失败断言',
            render: (_: unknown, row: EvaluationCase) =>
              failed(row).length
                ? failed(row).map((check) => (
                    <Tag color="red" key={check.field}>
                      {check.field}
                    </Tag>
                  ))
                : '-',
          },
          { title: 'Trace', dataIndex: 'trace_id', render: (value?: string) => value || '-' },
        ]}
      />
    </>
  )
}
