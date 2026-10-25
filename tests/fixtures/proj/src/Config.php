<?php

declare(strict_types=1);

namespace App\Core;

class Config
{
    private array $values = [];

    public function __construct(array $values)
    {
        $this->values = $values;
    }

    public function get(string $key, mixed $default = null): mixed
    {
        return $this->values[$key] ?? $default;
    }

    public function set(string $key, mixed $value): void
    {
        $this->values[$key] = $value;
    }
}
