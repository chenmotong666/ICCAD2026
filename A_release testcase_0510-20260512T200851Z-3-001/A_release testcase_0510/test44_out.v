module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test44/test44.v:9$4_Y , \$or$testcase/test44/test44.v:10$6_Y , \$xor$testcase/test44/test44.v:11$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n114;

  and   \$and$testcase/test44/test44.v:13$10  (n107, n2, n3);
  and   \$and$testcase/test44/test44.v:5$1  (n100, n2, n3);
  not   \$not$testcase/test44/test44.v:21$35  (n114, n4);
  or    \$or$testcase/test44/test44.v:14$11  (n108, n107, n5);
  or    \$or$testcase/test44/test44.v:6$2  (n101, n100, n4);
  or    \$or$testcase/test44/test44.v:23$15  (n22, n114, n100);
  xor   \$xor$testcase/test44/test44.v:15$12  (n21, n108, n6);
  not   \$not$testcase/test44/test44.v:7$32  (n102, n101);
  xor   \$xor$testcase/test44/test44.v:8$3  (n103, n102, n5);
  and   \$and$testcase/test44/test44.v:9$4  (\$and$testcase/test44/test44.v:9$4_Y , n103, n6);
  not   \$not$testcase/test44/test44.v:9$5  (n104, \$and$testcase/test44/test44.v:9$4_Y );
  or    \$or$testcase/test44/test44.v:10$6  (\$or$testcase/test44/test44.v:10$6_Y , n104, n7);
  not   \$not$testcase/test44/test44.v:10$7  (n105, \$or$testcase/test44/test44.v:10$6_Y );
  xor   \$xor$testcase/test44/test44.v:11$8  (\$xor$testcase/test44/test44.v:11$8_Y , n105, n8);
  not   \$not$testcase/test44/test44.v:11$9  (n20, \$xor$testcase/test44/test44.v:11$8_Y );

  assign n23 = n5;

endmodule
