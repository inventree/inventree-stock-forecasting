import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteExternalsPlugin } from 'vite-plugin-externals'


/**
 * The following libraries are externalized to avoid bundling them with the plugin.
 * These libraries are expected to be provided by the InvenTree core application.
 */
export const externalLibs : Record<string, string> = {
  react: 'React',
  'react-dom': 'ReactDOM',
  'ReactDom': 'ReactDOM',
  '@mantine/core': 'MantineCore',
  "@mantine/notifications": 'MantineNotifications',
};

// Just the keys of the externalLibs object
const externalKeys = Object.keys(externalLibs);

/**
 * Vite config to build the frontend plugin as an exported module.
 * This will be distributed in the 'static' directory of the plugin.
 */
export default defineConfig({
  plugins: [
    react({
      jsxRuntime: 'classic'
    }),
    viteExternalsPlugin(externalLibs),
  ],
  esbuild: {
    jsx: 'preserve',
  },
  build: {
    // minify: false,
    cssCodeSplit: false,
    manifest: true,
    sourcemap: true,
    rollupOptions: {
      preserveEntrySignatures: "exports-only",
      input: [
        './src/ForecastingPanel.tsx',
      ],
      output: [
        {
          dir: '../inventree_forecasting/static',
          globals: externalLibs,
          entryFileNames: '[name].js',
          assetFileNames: 'assets/[name].[ext]',
        },
        {
          dir: '../inventree_forecasting/static',
          globals: externalLibs,
          entryFileNames: '[name]-[hash].min.js',
          assetFileNames: 'assets/[name].[ext]',
        }
      ],
      external: externalKeys
    }
  },
  optimizeDeps: {
    exclude: externalKeys,
  },
})
