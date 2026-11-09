import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Descriptions,
  Drawer,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { downloadFile } from '../api/client'
import { useDocument, useDocuments, useDocumentVersion } from '../api/queries'
import type { DocumentSummary, DocumentVersion } from '../api/types'
import { ErrorAlert, QueryEmpty } from '../components/ListState'
import {
  formatBytes,
  formatDateTime,
  matchesKeyword,
  statusColor,
  uniqueOptions,
  versionLabel,
} from '../utils/format'
import { listPagination } from '../utils/table'

const visibilityColor: Record<string, string> = {
  tenant: 'green',
  staff_only: 'orange',
  restricted: 'red',
}

function downloadVersion(documentId: string, version: DocumentVersion) {
  void downloadFile(
    `/documents/${documentId}/versions/${version.id}/download`,
    version.source_filename ?? `${version.id}.txt`,
  )
}

export default function Knowledge() {
  const documents = useDocuments()
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [productType, setProductType] = useState<string>()
  const [visibility, setVisibility] = useState<string>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [drawerDocumentId, setDrawerDocumentId] = useState<string>()
  const [selectedVersion, setSelectedVersion] = useState<string>()

  const rows = documents.data ?? []
  const categories = useMemo(() => uniqueOptions(rows.map((row) => row.category)), [rows])
  const statuses = useMemo(() => uniqueOptions(rows.map((row) => row.status)), [rows])
  const productTypes = useMemo(() => uniqueOptions(rows.map((row) => row.product_type)), [rows])
  const visibilities = useMemo(() => uniqueOptions(rows.map((row) => row.visibility)), [rows])
  const detail = useDocument(drawerDocumentId, Boolean(drawerDocumentId))
  const versionDetail = useDocumentVersion(
    drawerDocumentId,
    selectedVersion,
    Boolean(selectedVersion),
  )

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (category && row.category !== category) return false
        if (status && row.status !== status) return false
        if (productType && row.product_type !== productType) return false
        if (visibility && row.visibility !== visibility) return false
        return matchesKeyword(keyword, [
          row.id,
          row.title,
          row.category,
          row.product_type,
          row.visibility,
          row.published_version?.label,
        ])
      }),
    [rows, category, status, productType, visibility, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [keyword, category, status, productType, visibility])

  function resetFilters() {
    setKeyword('')
    setCategory(undefined)
    setStatus(undefined)
    setProductType(undefined)
    setVisibility(undefined)
  }

  function closeDrawer() {
    setDrawerDocumentId(undefined)
    setSelectedVersion(undefined)
  }

  const hasFilters = Boolean(keyword || category || status || productType || visibility)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Knowledge Center
          </Typography.Title>
          <Typography.Text type="secondary">
            文档目录按分类与文档 ID 排列，打开版本抽屉可查看历史版本与解析正文。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            共 {filtered.length} / {rows.length} 个文档
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void documents.refetch()} loading={documents.isFetching}>
            刷新
          </Button>
        </Space>
      </Space>

      {documents.isError ? (
        <ErrorAlert title="文档目录加载失败" error={documents.error} onRetry={() => void documents.refetch()} />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索标题 / 分类 / 版本"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 260 }}
        />
        <Select
          allowClear
          placeholder="分类"
          value={category}
          onChange={setCategory}
          style={{ width: 150 }}
          options={categories.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="产品类型"
          value={productType}
          onChange={setProductType}
          style={{ width: 150 }}
          options={productTypes.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="Visibility"
          value={visibility}
          onChange={setVisibility}
          style={{ width: 150 }}
          options={visibilities.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="状态"
          value={status}
          onChange={setStatus}
          style={{ width: 130 }}
          options={statuses.map((value) => ({ value, label: value }))}
        />
        <Button onClick={resetFilters} disabled={!hasFilters}>
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={documents.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={documents.isError}
              loading={documents.isLoading}
              filtered={hasFilters}
              emptyText="暂无文档。执行 seed 后会写入 FAQ 演示文档与版本。"
              filteredText="没有匹配的文档，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 1080 }}
        columns={[
          {
            title: '文档',
            dataIndex: 'title',
            width: 280,
            render: (value: string | null | undefined, row: DocumentSummary) => (
              <Space orientation="vertical" size={0}>
                <Typography.Text strong>{value || '-'}</Typography.Text>
                <Typography.Text type="secondary" copyable style={{ fontSize: 12 }}>
                  {row.id}
                </Typography.Text>
              </Space>
            ),
          },
          { title: '分类', dataIndex: 'category', width: 110, render: (value?: string) => value || '-' },
          { title: '产品', dataIndex: 'product_type', width: 110, render: (value?: string) => value || '-' },
          {
            title: '可见性',
            dataIndex: 'visibility',
            width: 120,
            render: (value: string) => <Tag color={visibilityColor[value] ?? 'default'}>{value}</Tag>,
          },
          {
            title: '当前发布版本',
            dataIndex: 'published_version',
            width: 150,
            render: (version?: DocumentVersion | null) =>
              version ? <Tag color="blue">{versionLabel(version)}</Tag> : '-',
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
          },
          {
            title: '版本数',
            dataIndex: 'version_count',
            width: 80,
            render: (value: number) => value ?? 0,
          },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            width: 180,
            render: (value?: string | null) => formatDateTime(value),
          },
          {
            title: '操作',
            width: 120,
            render: (_: unknown, row: DocumentSummary) => (
              <Button size="small" onClick={() => setDrawerDocumentId(row.id)}>
                版本抽屉
              </Button>
            ),
          },
        ]}
      />

      <Drawer
        title="文档版本"
        width={760}
        open={Boolean(drawerDocumentId)}
        onClose={closeDrawer}
      >
        {detail.isError ? (
          <ErrorAlert title="文档详情加载失败" error={detail.error} onRetry={() => void detail.refetch()} />
        ) : null}
        {detail.data ? (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="文档" span={2}>
                {detail.data.title || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="文档 ID">{detail.data.id}</Descriptions.Item>
              <Descriptions.Item label="分类">{detail.data.category || '-'}</Descriptions.Item>
              <Descriptions.Item label="产品类型">{detail.data.product_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="Visibility">
                <Tag color={visibilityColor[detail.data.visibility] ?? 'default'}>{detail.data.visibility}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="文档状态">
                <Tag color={statusColor(detail.data.status)}>{detail.data.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="当前发布版本">
                {detail.data.published_version
                  ? versionLabel(detail.data.published_version)
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="更新时间" span={2}>
                {formatDateTime(detail.data.updated_at)}
              </Descriptions.Item>
            </Descriptions>

            <Table
              rowKey="id"
              size="small"
              dataSource={detail.data.versions}
              pagination={false}
              columns={[
                {
                  title: '版本',
                  dataIndex: 'label',
                  width: 110,
                  render: (_: unknown, row: DocumentVersion) => (
                    <Space orientation="vertical" size={0}>
                      <Tag color="blue">{versionLabel(row)}</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {row.id}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 110,
                  render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
                },
                {
                  title: '生效时间',
                  dataIndex: 'effective_from',
                  width: 120,
                  render: (value?: string | null) => value || '-',
                },
                {
                  title: '发布时间',
                  dataIndex: 'published_at',
                  width: 160,
                  render: (value?: string | null) => formatDateTime(value),
                },
                {
                  title: '原文件',
                  width: 160,
                  render: (_: unknown, row: DocumentVersion) => (
                    <Space orientation="vertical" size={0}>
                      <Typography.Text style={{ fontSize: 12 }}>{row.source_filename || '-'}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {formatBytes(row.size_bytes)}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '操作',
                  width: 170,
                  render: (_: unknown, row: DocumentVersion) => (
                    <Space size={4}>
                      <Button size="small" onClick={() => setSelectedVersion(row.id)}>
                        查看正文
                      </Button>
                      <Button
                        size="small"
                        disabled={!row.has_source_file}
                        onClick={() => downloadVersion(detail.data!.id, row)}
                      >
                        下载
                      </Button>
                    </Space>
                  ),
                },
              ]}
            />
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title={selectedVersion ? `版本正文 · ${selectedVersion}` : '版本正文'}
        open={Boolean(selectedVersion)}
        onCancel={() => setSelectedVersion(undefined)}
        footer={null}
        width={760}
        loading={versionDetail.isLoading}
      >
        {versionDetail.isError ? (
          <ErrorAlert title="版本正文加载失败" error={versionDetail.error} onRetry={() => void versionDetail.refetch()} />
        ) : null}
        {versionDetail.data ? (
          <Space orientation="vertical" size="small" style={{ width: '100%' }}>
            <Space wrap size={4}>
              <Tag color="blue">{versionLabel(versionDetail.data)}</Tag>
              <Tag color={statusColor(versionDetail.data.status)}>{versionDetail.data.status}</Tag>
              {versionDetail.data.error_code ? <Tag color="red">{versionDetail.data.error_code}</Tag> : null}
              <Button
                size="small"
                disabled={!versionDetail.data.has_source_file}
                onClick={() => downloadVersion(detail.data!.id, versionDetail.data!)}
              >
                下载原文件
              </Button>
            </Space>
            {versionDetail.data.error_message ? (
              <Typography.Text type="danger">{versionDetail.data.error_message}</Typography.Text>
            ) : null}
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
              {versionDetail.data.content_text || '（无正文）'}
            </Typography.Paragraph>
          </Space>
        ) : null}
      </Modal>
    </>
  )
}
