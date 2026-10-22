<?php
// EspoCRM refuses to deliver webhooks to private addresses unless they are allow-listed as host:port
// (Espo\Core\HttpClient\Util::matchUrlToAddressList). The hub's webhook endpoint lives on the compose network.
// Run once by the espocrm-config service: php /bootstrap/allow-webhooks.php hub:8000
// Edits data/config.php directly (a PHP file returning an array); `bin/command config:set` can only store scalars.

$file = '/var/www/html/data/config.php';
$config = include $file;
if (!is_array($config)) {
    fwrite(STDERR, "cannot read $file\n");
    exit(1);
}
$config['webhookAllowedAddressList'] = array_values(array_slice($argv, 1));
if (file_put_contents($file, "<?php\nreturn " . var_export($config, true) . ";\n") === false) {
    fwrite(STDERR, "cannot write $file\n");
    exit(1);
}
echo 'webhookAllowedAddressList = ' . json_encode($config['webhookAllowedAddressList']) . PHP_EOL;
