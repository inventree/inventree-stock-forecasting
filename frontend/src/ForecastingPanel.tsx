import { Accordion, Alert, Skeleton, Stack, Text, Title } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';

import { DataTable } from 'mantine-datatable';

// Import for type checking
import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import { LineChart } from '@mantine/charts';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';

const FORECASTING_URL : string = "plugin/stock-forecasting/forecast/";

export const chartData = [
  {
    date: 'Mar 22',
    Apples: 2890,
    Oranges: 2338,
    Tomatoes: 2452,
  },
  {
    date: 'Mar 23',
    Apples: 2756,
    Oranges: 2103,
    Tomatoes: 2402,
  },
  {
    date: 'Mar 24',
    Apples: 3322,
    Oranges: 986,
    Tomatoes: 1821,
  },
  {
    date: 'Mar 25',
    Apples: 3470,
    Oranges: 2108,
    Tomatoes: 2809,
  },
  {
    date: 'Mar 26',
    Apples: 3129,
    Oranges: 1726,
    Tomatoes: 2290,
  },
];

export function ForecastingChart({
  data,
}: {
  data: any[];
}) {

  console.log("entries:", data);

  return (
    <LineChart
      h={300}
      data={chartData}
      dataKey="date"
      series={[
        { name: 'Apples', color: 'indigo.6' },
        { name: 'Oranges', color: 'blue.6' },
        { name: 'Tomatoes', color: 'teal.6' },
      ]}
      curveType="linear"
    />
  );
}


export function ForecastingTable({
  data
}: {
  data: any[];
}) {

  // Keep an internal copy of the records, so we can sort the table
  const [ records, setRecords ] = useState<any[]>([]);

  useEffect(() => {
    setRecords(data);
  }, [data]);

  const columns = useMemo(() => {
    return [
      {
        accessor: 'date',
        title: 'Date',
        sortable: true,
        render: (record: any) => {
          if (!record.date) {
            return <Text c='red' fs="italic">No date specified</Text>
          } else {
            const m = dayjs(record.date);
            return m.format('YYYY-MM-DD');
          }
        }
      },
      {
        accessor: 'quantity',
        title: 'Quantity',
        sortable: true,
      },
      {
        accessor: 'label',
        title: 'Reference',
        sortable: true,
      },
      {
        accessor: 'description',
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
                    <ForecastingChart data={forecastingQuery.data?.entries ?? []} />
                </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="table">
                <Accordion.Control>
                    <Title order={4} c={primary} >Forecasting Table</Title>
                </Accordion.Control>
                <Accordion.Panel>
                  <ForecastingTable data={forecastingQuery.data?.entries ?? []} />
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