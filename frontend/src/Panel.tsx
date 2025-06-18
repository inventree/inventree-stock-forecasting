import { Accordion, Alert, Stack, Text, Title } from '@mantine/core';
import { useMemo } from 'react';

// Import for type checking
import { checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import { ApiEndpoints, apiUrl, ModelType } from '@inventreedb/ui';
import { BarChart } from '@mantine/charts';
import { IconCalendarTime } from '@tabler/icons-react';

export const chartData = [
  { month: 'January', Smartphones: 1200, Laptops: 900, Tablets: 200 },
  { month: 'February', Smartphones: 1900, Laptops: 1200, Tablets: 400 },
  { month: 'March', Smartphones: 400, Laptops: 1000, Tablets: 200 },
  { month: 'April', Smartphones: 1000, Laptops: 200, Tablets: 800 },
  { month: 'May', Smartphones: 800, Laptops: 1400, Tablets: 1200 },
  { month: 'June', Smartphones: 750, Laptops: 600, Tablets: 1000 },
];

export function ForecastingChart() {
  return (
    <BarChart
      h={300}
      data={chartData}
      dataKey="month"
      series={[
        { name: 'Smartphones', color: 'violet.6' },
        { name: 'Laptops', color: 'blue.6' },
        { name: 'Tablets', color: 'teal.6' },
      ]}
      tickLine="y"
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
        <Alert color='blue' icon={<IconCalendarTime />}>
            <Text>Provides stock forecasting information basd on scheduled orders</Text>
        </Alert>
        <Accordion multiple defaultValue={['chart']}>
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
                    TABLE DATA HERE?
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