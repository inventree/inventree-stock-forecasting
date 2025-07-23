// Import for type checking
import {
  checkPluginVersion,
  getDetailUrl,
  type InvenTreePluginContext,
  type ModelType,
  navigateToLink
} from '@inventreedb/ui';
import { type ChartTooltipProps, LineChart } from '@mantine/charts';
import {
  Accordion,
  Alert,
  Anchor,
  Button,
  Divider,
  Group,
  Menu,
  Paper,
  Select,
  Skeleton,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  IconExclamationCircle,
  IconFileDownload,
  IconInfoCircle
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { useCallback, useEffect, useMemo, useState } from 'react';

const FORECASTING_URL: string = 'plugin/stock-forecasting/forecast/';

function formatDecimal(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return '-';
  }

  const formatter = new Intl.NumberFormat(navigator.language, {
    style: 'decimal',
    maximumFractionDigits: 2,
    minimumFractionDigits: 0
  });

  return formatter.format(value);
}

function ChartTooltip({ label, payload }: Readonly<ChartTooltipProps>) {
  if (!payload) {
    return null;
  }

  if (label && typeof label == 'number') {
    label = dayjs(label).format('YYYY-MM-DD');
  }

  const quantity = payload.find((item) => item.name == 'quantity');
  const minimum = payload.find((item) => item.name == 'minimum');
  const maximum = payload.find((item) => item.name == 'maximum');

  return (
    <Paper px='md' py='sm' withBorder shadow='md' radius='md'>
      <Text key='title'>{label}</Text>
      <Divider />
      {quantity?.value && (
        <Text key='scheduled' c={quantity?.color} fz='sm'>
          Forecast : {formatDecimal(quantity?.value)}
        </Text>
      )}
      {maximum?.value && (
        <Text key='maximum' c={maximum?.color} fz='sm'>
          Maximum : {formatDecimal(maximum?.value)}
        </Text>
      )}
      {minimum?.value && (
        <Text key='minimum' c={minimum?.color} fz='sm'>
          Minimum : {formatDecimal(minimum?.value)}
        </Text>
      )}
    </Paper>
  );
}

export function ForecastingChart({
  entries,
  initialStock,
  minimumStock,
  maximumStock
}: {
  entries: any[];
  initialStock: number;
  minimumStock: number;
  maximumStock: number;
}) {
  // Construct chart data based on the provided entries
  const chartData: any[] = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Set date bounds for the chart
    let minDate: Date = new Date();
    let maxDate: Date = new Date();

    // Track min / max forecast stock levels
    let stock: number = initialStock;
    let minStock: number = stock;
    let maxStock: number = stock;

    // First, iterate through the entries to find the "speculative" entries
    // Here, "speculative" means either:
    // - Entries which do not have an associated date
    // - Entries which have a date in the past (i.e. before today)
    entries.forEach((entry) => {
      if (entry.date == null || new Date(entry.date) < today) {
        if (entry.quantity < 0) {
          minStock += entry.quantity;
        } else if (entry.quantity > 0) {
          maxStock += entry.quantity;
        }
      }
    });

    // Construct an initial entry (i.e. current stock level as of *today*)
    const chartEntries: any[] = [
      {
        date: today.valueOf(),
        delta: 0,
        quantity: stock,
        minimum: minStock,
        maximum: maxStock
      }
    ];

    // Now, iterate through the entries to construct the chart data
    // At this point, only consider entries which have a recorded date
    entries
      .filter((entry) => !!entry.date)
      .forEach((entry) => {
        const date = new Date(entry.date);

        // If the date is before today, skip it
        if (date < today) {
          return;
        }

        // Update date limits
        if (date < minDate) {
          minDate = date;
        }

        if (date > maxDate) {
          maxDate = date;
        }

        // Update stock levels based on the entry
        stock += entry.quantity;
        minStock += entry.quantity;
        maxStock += entry.quantity;

        chartEntries.push({
          ...entry,
          date: new Date(entry.date).valueOf(),
          quantity: stock,
          minimum: minStock,
          maximum: maxStock,
          lowStockThreshold: minimumStock,
          highStockThreshold: maximumStock
        });
      });

    return chartEntries;
  }, [entries, initialStock, minimumStock, maximumStock]);

  // Calculate date limits of the chart
  const chartLimits: number[] = useMemo(() => {
    let minDate: Date = new Date();
    let maxDate: Date = new Date();

    if (chartData.length > 0) {
      minDate = new Date(chartData[0].date);
      maxDate = new Date(chartData[chartData.length - 1].date);
    }

    // Expand limits by one day on either side
    minDate.setDate(minDate.getDate() - 1);
    maxDate.setDate(maxDate.getDate() + 1);

    return [minDate.valueOf(), maxDate.valueOf()];
  }, [chartData]);

  const chartSeries: any[] = useMemo(() => {
    const series: any[] = [
      {
        name: 'minimum',
        label: 'Minimum',
        color: 'yellow.6'
      },
      {
        name: 'maximum',
        label: 'Maximum',
        color: 'teal.6'
      },
      {
        name: 'quantity',
        label: 'Quantity',
        color: 'blue.6'
      }
    ];

    return series;
  }, [minimumStock, maximumStock]);

  const referenceLines: any[] = useMemo(() => {
    const lines: any[] = [];

    lines.push({
      y: minimumStock ?? 0,
      label: 'Minimum Stock',
      color: 'red.6'
    });

    if ((maximumStock ?? 0) > 0) {
      lines.push({
        y: maximumStock,
        label: 'Maximum Stock',
        color: 'red.3'
      });
    }

    return lines;
  }, [minimumStock, maximumStock]);

  // No useful information to display
  if (chartData.length <= 1) {
    return (
      <Alert
        color='yellow'
        title='Insufficient Information'
        icon={<IconInfoCircle />}
      >
        <Text>
          The available forecasting data is insufficient to display a meaningful
          chart. Please ensure that there are future entries with specified
          dates.
        </Text>
      </Alert>
    );
  }

  return (
    <LineChart
      h={500}
      data={chartData}
      dataKey='date'
      withLegend
      withYAxis
      tooltipProps={{
        content: ({ label, payload }) => (
          <ChartTooltip label={label} payload={payload} />
        )
      }}
      yAxisLabel='Forecast Quantity'
      xAxisLabel='Date'
      xAxisProps={{
        domain: chartLimits,
        scale: 'time',
        type: 'number',
        tickFormatter: (value: number) => {
          return dayjs(value).format('YYYY-MM-DD');
        }
      }}
      series={chartSeries}
      referenceLines={referenceLines}
    />
  );
}

export function ForecastingTable({
  entries,
  context
}: {
  entries: any[];
  context: InvenTreePluginContext;
}) {
  const [sortStatus, setSortStatus] = useState<DataTableSortStatus<any>>({
    columnAccessor: 'date',
    direction: 'asc'
  });

  // Keep an internal copy of the records, so we can sort the table
  const [records, setRecords] = useState<any[]>([]);

  useEffect(() => {
    const sortedEntries = [...entries];

    if (sortStatus.columnAccessor) {
      sortedEntries.sort((a, b) => {
        let aValue = a[sortStatus.columnAccessor];
        let bValue = b[sortStatus.columnAccessor];

        // Special sorting for these columns
        if (sortStatus.columnAccessor === 'date') {
          aValue = aValue ? new Date(aValue).valueOf() : 0;
          bValue = bValue ? new Date(bValue).valueOf() : 0;
        } else if (sortStatus.columnAccessor === 'quantity') {
          aValue = parseFloat(aValue) || 0;
          bValue = parseFloat(bValue) || 0;
        }

        if (aValue < bValue) {
          return sortStatus.direction === 'desc' ? 1 : -1;
        } else if (aValue > bValue) {
          return sortStatus.direction === 'desc' ? -1 : 1;
        } else {
          return 0;
        }
      });
    }

    setRecords(sortedEntries);
  }, [entries, sortStatus]);

  const columns = useMemo(() => {
    return [
      {
        accessor: 'date',
        title: 'Date',
        sortable: true,
        render: (record: any) => {
          // No date specified
          if (!record.date) {
            return (
              <Text c='red' fs='italic'>
                No date specified
              </Text>
            );
          }

          // Date is specified, but in the past
          if (dayjs(record.date).isBefore(dayjs())) {
            return (
              <Text c='red' fs='italic'>
                {record.date}
              </Text>
            );
          }

          return <Text>{record.date}</Text>;
        }
      },
      {
        accessor: 'quantity',
        title: 'Quantity Change',
        sortable: true,
        render: (record: any) => {
          let prefix: string = '';

          if (record.quantity > 0) {
            prefix = '+';
          }

          return (
            <Text>
              {prefix}
              {record.quantity}
            </Text>
          );
        }
      },
      {
        accessor: 'label',
        title: 'Reference',
        sortable: true,
        render: (record: any) => {
          const url = getDetailUrl(record.model_type, record.model_id);

          if (url) {
            return (
              <Anchor
                onClick={(event: any) =>
                  navigateToLink(url, context.navigate, event)
                }
                href={url}
                target='_blank'
                rel='noopener noreferrer'
              >
                {record.label}
              </Anchor>
            );
          } else {
            return <Text>{record.label}</Text>;
          }
        }
      },
      {
        accessor: 'model_type',
        title: 'Model Type',
        sortable: true,
        render: (record: any) => {
          // If the model type is not specified, return an empty string
          if (!record.model_type) {
            return '';
          }

          // Access the model information
          return (
            context.modelInformation[
              record.model_type as ModelType
            ]?.label?.() ?? record.model_type
          );
        }
      },
      {
        accessor: 'title',
        title: 'Description',
        sortable: false
      }
    ];
  }, [context.modelInformation, context.locale]);

  return (
    <DataTable
      columns={columns}
      records={records}
      sortStatus={sortStatus}
      onSortStatusChange={setSortStatus}
      withColumnBorders
      withTableBorder
      striped
    />
  );
}

/**
 * Render a custom panel with the provided context.
 * Refer to the InvenTree documentation for the context interface
 * https://docs.inventree.org/en/stable/extend/plugins/ui/#plugin-context
 */
function InvenTreeForecastingPanel({
  context
}: {
  context: InvenTreePluginContext;
}) {
  const downloadData = useCallback(
    (format: string) => {
      let url = `${FORECASTING_URL}?part=${context.id}&export=${format}`;

      if (context.host) {
        url = `${context.host}${url}`;
      } else {
        url = `${window.location.origin}/${url}`;
      }

      window.open(url, '_blank');
    },
    [context.host, context.id]
  );

  const [ includeVariants, setIncludeVariants ] = useState<boolean>(false);

  const forecastingQuery = useQuery(
    {
      enabled: !!context.id,
      queryKey: ['forecasting', context.id, includeVariants],
      refetchOnMount: true,
      refetchOnWindowFocus: false,
      queryFn: async () => {
        return (
          context.api
            ?.get(`/${FORECASTING_URL}`, {
              params: {
                part: context.id,
                include_variants: includeVariants
              }
            })
            .then((response: any) => {
              return response.data;
            })
            .catch(() => {
              return {};
            }) ?? {}
        );
      }
    },
    context.queryClient
  );

  const hasForecastingData: boolean = useMemo(() => {

    if (forecastingQuery.isFetching || forecastingQuery.isLoading) {
      return false;
    }

    return (forecastingQuery.data?.entries?.length ?? 0) > 0;
  }, [
    forecastingQuery.isFetching,
    forecastingQuery.isLoading,
    forecastingQuery.data
  ]);

  const primary: string = useMemo(() => {
    return context.theme.primaryColor;
  }, [context.theme.primaryColor]);

  return (
    <>
      <Stack gap='xs'>
        <Paper withBorder p='sm' m='sm'>
          <Group gap='xs' justify='space-apart'>
            <Select
              label={"Include Variant Parts"}
              value={includeVariants ? 'true' : 'false'}
              onChange={(value) => {
                setIncludeVariants(value === 'true');
              }}
              data={[
                {
                  value: 'false',
                  label: 'No'
                },
                {
                  value: 'true',
                  label: 'Yes'
                }
              ]}
            />
          </Group>
        </Paper>
      {(forecastingQuery.isLoading || forecastingQuery.isFetching) && (
        <Skeleton animate height={300} />
      )}
      {forecastingQuery.isError && (
          <Alert
          color='red'
          title='Error Loading Data'
          icon={<IconExclamationCircle />}
        >
          <Text>{forecastingQuery.error.message}</Text>
        </Alert>
      )}
      {hasForecastingData ? (
        <Accordion multiple defaultValue={['chart', 'table']}>
          <Accordion.Item value='chart'>
            <Accordion.Control>
              <Title order={4} c={primary}>
                Forecasting Chart
              </Title>
            </Accordion.Control>
            <Accordion.Panel>
              <ForecastingChart
                entries={forecastingQuery.data?.entries ?? []}
                initialStock={forecastingQuery.data?.in_stock ?? 0}
                minimumStock={forecastingQuery.data?.min_stock ?? 0}
                maximumStock={forecastingQuery.data?.max_stock ?? 0}
              />
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item value='table'>
            <Accordion.Control>
              <Title order={4} c={primary}>
                Forecasting Data
              </Title>
            </Accordion.Control>
            <Accordion.Panel>
              <Stack gap='xs'>
                <ForecastingTable
                  entries={forecastingQuery.data?.entries ?? []}
                  context={context}
                />
                <Group gap='xs' justify='flex-end'>
                  <Menu>
                    <Menu.Target>
                      <Button leftSection={<IconFileDownload />}>Export</Button>
                    </Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Item onClick={() => downloadData('csv')}>
                        CSV
                      </Menu.Item>
                      <Menu.Item onClick={() => downloadData('xls')}>
                        XLS
                      </Menu.Item>
                      <Menu.Item onClick={() => downloadData('xlsx')}>
                        XLSX
                      </Menu.Item>
                    </Menu.Dropdown>
                  </Menu>
                </Group>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
       ) : ( 
        <Alert color='yellow' title='No Data Available' icon={<IconInfoCircle />}>
          <Text>
            There is no forecasting data available for the selected part.
          </Text>
        </Alert>
      )}
      </Stack>
    </>
  );
}

// This is the function which is called by InvenTree to render the actual panel component
export function renderInvenTreeForecastingPanel(
  context: InvenTreePluginContext
) {
  checkPluginVersion(context);
  return <InvenTreeForecastingPanel context={context} />;
}
