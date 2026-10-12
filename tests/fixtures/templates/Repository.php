<?php

declare(strict_types=1);

namespace App\Jobs;

use App\Core\Config;
use App\Pipeline\Engine;

class Repository
{
    public function __construct(private Config $config, private Engine $engine)
    {
    }

    public function handle(array $jobs): array
    {
        $result = $this->engine->run($jobs);
        $table = $this->config->get('table', 'myproject_jobs');
        return ['table' => $table, 'result' => $result];
    }

    public function find(int $id): array
    {
        $sql = "SELECT id, name, status FROM myproject_jobs WHERE id = :id";
        return ['sql' => $sql, 'id' => $id];
    }
}
