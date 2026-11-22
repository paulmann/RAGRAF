<?php

declare(strict_types=1);

namespace App\Pipeline;

use App\Core\Config;

class Engine
{
    private Config $config;

    public function __construct(Config $config)
    {
        $this->config = $config;
    }

    public function run(array $jobs): array
    {
        $done = [];
        foreach ($jobs as $job) {
            $name = $job['name'] ?? 'unknown';
            $limit = (int) $this->config->get('limit', 10);
            if (count($done) >= $limit) {
                break;
            }
            $done[] = $this->formatStage($name);
        }
        return $done;
    }

    private function formatStage(string $name): string
    {
        $prefix = $this->config->get('prefix', 'stage');
        return $prefix . ':' . trim($name);
    }
}
