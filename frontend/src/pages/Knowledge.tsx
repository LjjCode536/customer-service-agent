import { useEffect, useMemo, useState } from 'react'
import { Button, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useKnowledge } from '../api/queries'
import type { KnowledgeDoc } from '../api/types'
import { ErrorAlert, QueryEmpty } from '../components/ListState'
import { formatDateTime, matchesKeyword, statusColor, uniqueOptions } from '../utils/format'
import { listPagination } from '../utils/table'

export default function Knowledge() {
  const knowledge = useKnowledge()
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)

  const rows = knowledge.data ?? []
  const categories = useMemo(() => uniqueOptions(rows.map((row) => row.category)), [rows])
  const statuses = useMemo(() => uniqueOptions(rows.map((row) => row.status)), [rows])

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (category && row.category !== category) return false
        if (status && row.status !== status) return false
        return matchesKeyword(keyword, [row.title, row.category, row.product_type, row.version, row.content, ...row.keywords])
      }),
    [rows, category, status, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [keyword, category, status])

  function resetFilters() {
    setKeyword('')
    setCategory(undefined)
    setStatus(undefined)
  }

  const hasFilters = Boolean(keyword || category || status)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Knowledge Center
          </Typography.Title>
          <Typography.Text type="secondary">
            知识版本按分类与生效时间排列，展开行可查看正文与关键词。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            共 {filtered.length} / {rows.length} 条
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void knowledge.refetch()} loading={knowledge.isFetching}>
            刷新
          </Button>
        </Space>
      </Space>

      {knowledge.isError ? (
        <ErrorAlert title="知识库加载失败" error={knowledge.error} onRetry={() => void knowledge.refetch()} />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索标题 / 正文 / 关键词"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 300 }}
        />
        <Select
          allowClear
          placeholder="分类"
          value={category}
          onChange={setCategory}
          style={{ width: 180 }}
          options={categories.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="状态"
          value={status}
          onChange={setStatus}
          style={{ width: 140 }}
          options={statuses.map((value) => ({ value, label: value }))}
        />
        <Button onClick={resetFilters} disabled={!hasFilters}>
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={knowledge.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={knowledge.isError}
              loading={knowledge.isLoading}
              filtered={hasFilters}
              emptyText="暂无知识文档。执行 seed 后会写入 FAQ 演示数据。"
              filteredText="没有匹配的知识文档，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 980 }}
        expandable={{
          expandedRowRender: (row: KnowledgeDoc) => (
            <Space orientation="vertical" size="small" style={{ width: '100%' }}>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{row.content || '（无正文）'}</Typography.Paragraph>
              <Space wrap size={4}>
                <Typography.Text type="secondary">关键词：</Typography.Text>
                {row.keywords.length ? (
                  row.keywords.map((item) => <Tag key={item}>{item}</Tag>)
                ) : (
                  <Typography.Text type="secondary">-</Typography.Text>
                )}
              </Space>
            </Space>
          ),
        }}
        columns={[
          { title: '标题', dataIndex: 'title', width: 280, render: (value?: string) => value || '-' },
          { title: '分类', dataIndex: 'category', width: 160, render: (value?: string) => value || '-' },
          { title: '产品', dataIndex: 'product_type', width: 140, render: (value?: string) => value || '-' },
          { title: '版本', dataIndex: 'version', width: 100, render: (value?: string) => (value ? <Tag>{value}</Tag> : '-') },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (value?: string) =>
              value ? <Tag color={statusColor(value)}>{value}</Tag> : '-',
          },
          {
            title: '生效时间',
            dataIndex: 'effective_from',
            width: 180,
            render: (value?: string | null) => formatDateTime(value),
          },
          {
            title: '关键词数',
            width: 100,
            render: (_: unknown, row: KnowledgeDoc) => row.keywords.length,
          },
        ]}
      />
    </>
  )
}
