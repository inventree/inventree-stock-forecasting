
import { Box, Group, MantineProvider, Paper, Text} from '@mantine/core';
import { DateValue, MonthPickerInput } from '@mantine/dates';
import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import dayjs from 'dayjs';

// const SCHEDULING_URL = "plugin/scheduling/schedule/";

function SchedulingPanel({context}: {context: any}) {

    // Plugin settings object
    const pluginSettings = useMemo(() => context?.context?.settings ?? {}, [context]);

    useEffect(() => {
        // Kludge until we use the context and plugin settings
    }, [pluginSettings]);

    // Starting date for the order history
    const [startDate, setStartDate ] = useState<Date>(
        dayjs().subtract(1, 'year').toDate()
      );
    
    // Ending date for the order history
      const [endDate, setEndDate] = useState<Date>(
        dayjs().add(1, 'month').toDate()
      );

    return (
        <>
        <Paper withBorder p="sm" m="sm">
        <Group gap="xs" justify='space-apart' grow>
            <Group gap="xs">
            <MonthPickerInput
            value={startDate}
            label={`Start Date`}
            onChange={(value: DateValue) => {
                if (value && value < endDate) {
                    setStartDate(value);
                }
            }}
            />
            <MonthPickerInput
            value={endDate}
            label={`End Date`}
            onChange={(value: DateValue) => {
                if (value && value > startDate) {
                    setEndDate(value);
                }
            }}
            />
            </Group>
            </Group>
            </Paper>
        <Paper withBorder p="sm" m="sm">
            <Box pos="relative">
                <Text>Hello world</Text>
            </Box>
        </Paper>
        </>
    );
}


/**
 * Render the SchedulingPanel component
 * 
 * @param target - The target HTML element to render the panel into
 * @param context - The context object to pass to the panel
 */
export function renderPanel(target: HTMLElement, context: any) {

    createRoot(target).render(
        <MantineProvider>
            <SchedulingPanel context={context}/>
        </MantineProvider>
    )

}