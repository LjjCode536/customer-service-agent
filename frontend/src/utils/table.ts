/** Shared Ant Design list pagination so every console page behaves the same. */
export function listPagination(
  page: number,
  pageSize: number,
  setPage: (page: number) => void,
  setPageSize: (pageSize: number) => void,
  unit = '条',
) {
  return {
    current: page,
    pageSize,
    showSizeChanger: true,
    pageSizeOptions: [10, 20, 50],
    showTotal: (total: number) => `共 ${total} ${unit}`,
    onChange: (current: number, size: number) => {
      setPage(current)
      setPageSize(size)
    },
  }
}
