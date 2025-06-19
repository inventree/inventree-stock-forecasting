import { Accordion, Alert, Stack, Title } from '@mantine/core';
import { useEffect, useMemo } from 'react';

import { DataTable } from 'mantine-datatable';

// Import for type checking
import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import { ApiEndpoints, apiUrl, ModelType } from '@inventreedb/ui';
import { LineChart } from '@mantine/charts';
import { useQuery } from '@tanstack/react-query';

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

export function ForecastingChart() {
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


export function ForecastingTable() {

  const columns = useMemo(() => {
    return [
      {
        accessor: 'date',
        title: 'Date',
      },
      {
        accessor: 'quantity',
        title: 'Quantity',
      },
      {
        accessor: 'label',
        title: 'Reference',
      },
      {
        accessor: 'description',
        title: 'Description',
      }
    ]
  }, []);

  return (
    <DataTable 
      columns={columns}
      records={[]}
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

  useEffect(() => {
    console.log("context:", context);
  }, [context]);

    const partId = useMemo(() => {
        return context.model == ModelType.part ? context.id || null: null;
    }, [context.model, context.id]);


    // Custom form to edit the selected part
    const editPartForm = context.forms.edit({
        url: apiUrl(ApiEndpoints.part_list, partId),
        title: "Edit Part",
        preFormContent: (
            <Alert title="Custom Plugin Form" color="blue">
                This is a custom form launched from within a plugin!
            </Alert>
        ),
        fields: {
            name: {},
            description: {},
            category: {},
        },
        successMessage: null,
        onFormSuccess: () => {
            // notifications.show({
            //     title: 'Success',
            //     message: 'Part updated successfully!',
            //     color: 'green',
            // });
        }
    });

  const forecastingQuery = useQuery(
    {
      enabled: !!context.id,
      queryKey: ['forecasting', context.id],
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      queryFn: async () => {
        return context.api?.get(`/${FORECASTING_URL}`, {
          params: {
            part: context.id,
          }
        }).then((response: any) => {
          return response.data;
        }).catch(() => {
          return [];
        }) ?? [];
      }
    },
    context.queryClient
  );

  useEffect(() => {
    console.log("data:", forecastingQuery.data);
  }, [forecastingQuery.data]);

    // Custom callback function example
    // const openForm = useCallback(() => {
    //     editPartForm?.open();
    // }, [editPartForm]);

    // // Navigation functionality example
    // const gotoDashboard = useCallback(() => {
    //     context.navigate('/home');
    // }, [context]);

    const primary : string = useMemo(() => {
        return context.theme.primaryColor;
    }, [context.theme.primaryColor]);

    return (
        <>
        {editPartForm.modal}
        <Stack gap="xs">
        {/* <Alert color='blue' icon={<IconCalendarTime />}>
            <Text>Provides stock forecasting information basd on scheduled orders</Text>
        </Alert> */}
        <Accordion multiple defaultValue={['chart', 'table']}>
            <Accordion.Item value="chart">
                <Accordion.Control>
                    <Title order={4} c={primary} >Forecasting Chart</Title>
                </Accordion.Control>
                <Accordion.Panel>
                    <ForecastingChart />
                </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="table">
                <Accordion.Control>
                    <Title order={4} c={primary} >Forecasting Table</Title>
                </Accordion.Control>
                <Accordion.Panel>
                  <ForecastingTable />
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