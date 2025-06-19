import { Accordion, Alert, Divider, Paper, Skeleton, Stack, Text, Title } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';

import { DataTable } from 'mantine-datatable';

// Import for type checking
import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import { ChartTooltipProps, LineChart } from '@mantine/charts';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';

const FORECASTING_URL : string = "plugin/stock-forecasting/forecast/";


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
      <Text key='scheduled' c={quantity?.color} fz='sm'>
        Forecast : {quantity?.value}
      </Text>
      {maximum != quantity && (
        <Text key='maximum' c={maximum?.color} fz='sm'>
          Maximum : {maximum?.value}
        </Text>
      )}
      {minimum != quantity && (
        <Text key='minimum' c={minimum?.color} fz='sm'>
          Minimum : {minimum?.value}
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
  initialStock: number,
  minimumStock: number,
  maximumStock: number
}) {

  // Construct chart data based on the provided entries
  const chartData : any[] = useMemo(() => {

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Set date bounds for the chart
    let minDate: Date = new Date();
    let maxDate: Date = new Date();

    // Track min / max forecast stock levels
    let stock: number = initialStock;
    let minStock : number = stock;
    let maxStock : number = stock;

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
        maximum: maxStock,
        lowStockThreshold: minimumStock,
        highStockThreshold: maximumStock
      }
    ];

    // Now, iterate through the entries to construct the chart data
    // At this point, only consider entries which have a recorded date
    entries.filter((entry) => !!entry.date).forEach((entry) => {
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
        highStockThreshold: maximumStock,
      })
    });

    return chartEntries;

  }, [entries, initialStock, minimumStock, maximumStock]);

  // Calculate date limits of the chart
  const chartLimits: number[] = useMemo(() => {

    let minDate : Date = new Date();
    let maxDate : Date = new Date();

    if (chartData.length > 0) {
      minDate = new Date(chartData[0].date);
      maxDate = new Date(chartData[chartData.length - 1].date);
    }

    // Expand limits by one day on either side
    minDate.setDate(minDate.getDate() - 1);
    maxDate.setDate(maxDate.getDate() + 1);

    return [minDate.valueOf(), maxDate.valueOf()];

  }, [chartData])

  const chartSeries: any[] = useMemo(() => {

    const series: any[] = [
      {
        name: 'quantity',
        label: 'Quantity',
        color: 'blue.6',
      },
      {
        name: 'minimum',
        label: 'Minimum',
        color: 'yellow.6',
      },
      {
        name: 'maximum',
        label: 'Maximum',
        color: 'teal.6',
      }
    ];

    if (minimumStock > 0) {
      series.push({
        name: 'lowStockThreshold',
        label: 'Low Stock Threshold',
        color: 'red.6',
      });
    }

    if (maximumStock > 0) {
      series.push({
        name: 'highStockThreshold',
        label: 'High Stock Threshold',
        color: 'red.6',
      });
    }

    return series;

  }, [minimumStock, maximumStock]);

  return (
    <LineChart
      h={500}
      data={chartData}
      dataKey="date"
      withLegend
      withYAxis
      tooltipProps={{
        content: ({ label, payload }) => (
          <ChartTooltip label={label} payload={payload} />
        )
      }}
      yAxisLabel="Forecast Quantity"
      xAxisLabel="Date"
      xAxisProps={{
        domain: chartLimits,
        scale: 'time',
        type: 'number',
        tickFormatter: (value: number) => {
          return dayjs(value).format('YYYY-MM-DD');
        }
      }}
      series={chartSeries}
    />
  );
}


export function ForecastingTable({
  entries
}: {
  entries: any[];
}) {

  // Keep an internal copy of the records, so we can sort the table
  const [ records, setRecords ] = useState<any[]>([]);

  useEffect(() => {
    setRecords(entries);
  }, [entries]);

  // TODO: Add "total quantity" column

  const columns = useMemo(() => {
    return [
      {
        accessor: 'date',
        title: 'Date',
        sortable: true,
        render: (record: any) => {
          // No date specified
          if (!record.date) {
            return <Text c='red' fs="italic">No date specified</Text>
          }

          // Date is specified, but in the past
          if (dayjs(record.date).isBefore(dayjs())) {
            return <Text c='red' fs="italic">{record.date}</Text>
          }
          
          return <Text>{record.date}</Text>
        }
      },
      {
        accessor: 'quantity',
        title: 'Quantity Change',
        sortable: true,
        render: (record: any) => {
          let prefix : string = '';
          
          if (record.quantity > 0) {
            prefix = '+';
          }

          return <Text>{prefix}{record.quantity}</Text>;
        }
      },
      {
        accessor: 'label',
        title: 'Reference',
        sortable: true,
      },
      {
        accessor: 'title',
        title: 'Description',
        sortable: true,
      }
    ]
  }, []);

  return (
    <DataTable 
      columns={columns}
      records={records}
    />
  )
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

    // Custom form to edit the selected part
    // const editPartForm = context.forms.edit({
    //     url: apiUrl(ApiEndpoints.part_list, partId),
    //     title: "Edit Part",
    //     preFormContent: (
    //         <Alert title="Custom Plugin Form" color="blue">
    //             This is a custom form launched from within a plugin!
    //         </Alert>
    //     ),
    //     fields: {
    //         name: {},
    //         description: {},
    //         category: {},
    //     },
    //     successMessage: null,
    //     onFormSuccess: () => {
    //         // notifications.show({
    //         //     title: 'Success',
    //         //     message: 'Part updated successfully!',
    //         //     color: 'green',
    //         // });
    //     }
    // });

  const forecastingQuery = useQuery(
    {
      enabled: !!context.id,
      queryKey: ['forecasting', context.id],
      refetchOnMount: true,
      refetchOnWindowFocus: false,
      queryFn: async () => {
        return context.api?.get(`/${FORECASTING_URL}`, {
          params: {
            part: context.id,
          }
        }).then((response: any) => {
          return response.data;
        }).catch(() => {
          return {};
        }) ?? {};
      }
    },
    context.queryClient
  );

  const hasForecastingData : boolean = useMemo(() => {
    return (forecastingQuery.data?.entries?.length ?? 0) > 0;
  }, [forecastingQuery.data]);


    const primary : string = useMemo(() => {
        return context.theme.primaryColor;
    }, [context.theme.primaryColor]);

    if (forecastingQuery.isLoading || forecastingQuery.isFetching) {
      return <Skeleton animate height={300} />;
    }

    if (forecastingQuery.isError) {
      return (
        <Alert color="red" title="Error loading forecasting data">
          <Text>{forecastingQuery.error.message}</Text>
        </Alert>
      );
    }

    if (!hasForecastingData) {
      return (
        <Alert color="yellow" title="No forecasting data available">
          <Text>
            There is no forecasting data available for the selected part.
          </Text>
        </Alert>
      )
    }

    return (
        <>
        <Stack gap="xs">
        <Accordion multiple defaultValue={['chart', 'table']}>
            <Accordion.Item value="chart">
                <Accordion.Control>
                    <Title order={4} c={primary} >Forecasting Chart</Title>
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
            <Accordion.Item value="table">
                <Accordion.Control>
                    <Title order={4} c={primary} >Forecasting Data</Title>
                </Accordion.Control>
                <Accordion.Panel>
                  <ForecastingTable entries={forecastingQuery.data?.entries ?? []} />
                </Accordion.Panel>
            </Accordion.Item>
        </Accordion>
        </Stack>
        </>
    );
}

// This is the function which is called by InvenTree to render the actual panel component
export function renderInvenTreeForecastingPanel(context: InvenTreePluginContext) {
    checkPluginVersion(context);
    return <InvenTreeForecastingPanel context={context} />;
}